use std::{env, net::SocketAddr, time::Duration};

use axum::{
    body::{Body, Bytes},
    extract::State,
    http::{header, HeaderMap, Method, StatusCode, Uri},
    response::{IntoResponse, Response},
    routing::{any, get},
    Json, Router,
};
use futures_util::StreamExt;
use serde::Serialize;
use tower_http::{cors::CorsLayer, trace::TraceLayer};
use tracing::{error, info};

#[derive(Clone)]
struct AppState {
    client: reqwest::Client,
    base_url: String,
    default_api_key: Option<String>,
    default_bearer_token: Option<String>,
}

#[derive(Serialize)]
struct MetaHealth {
    service: &'static str,
    status: &'static str,
    upstream_url: String,
}

#[derive(Serialize)]
struct ErrorBody {
    error: String,
}

#[tokio::main]
async fn main() -> anyhow::Result<()> {
    dotenvy::dotenv().ok();
    init_tracing();

    let host = env::var("SUPARAG_HOST").unwrap_or_else(|_| "0.0.0.0".to_string());
    let port: u16 = env::var("SUPARAG_PORT")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(8080);

    let upstream_timeout_secs: u64 = env::var("SUPARAG_UPSTREAM_TIMEOUT_SECS")
        .ok()
        .and_then(|v| v.parse().ok())
        .unwrap_or(300);

    let state = AppState {
        client: reqwest::Client::builder()
            .timeout(Duration::from_secs(upstream_timeout_secs))
            .build()?,
        base_url: env::var("SUPARAG_LIGHTRAG_BASE_URL")
            .unwrap_or_else(|_| "http://127.0.0.1:9621".to_string()),
        default_api_key: env::var("SUPARAG_DEFAULT_API_KEY").ok(),
        default_bearer_token: env::var("SUPARAG_DEFAULT_BEARER_TOKEN").ok(),
    };

    let app = Router::new()
        .route("/", get(root))
        .route("/_meta/health", get(meta_health))
        .route("/*path", any(proxy_any))
        .layer(TraceLayer::new_for_http())
        .layer(CorsLayer::permissive())
        .with_state(state);

    let addr: SocketAddr = format!("{}:{}", host, port).parse()?;
    info!("supaRAG listening on {}", addr);

    let listener = tokio::net::TcpListener::bind(addr).await?;
    axum::serve(listener, app).await?;
    Ok(())
}

fn init_tracing() {
    let _ = tracing_subscriber::fmt()
        .with_env_filter(
            tracing_subscriber::EnvFilter::try_from_default_env()
                .unwrap_or_else(|_| "info,suparag=info".into()),
        )
        .try_init();
}

async fn root() -> impl IntoResponse {
    Json(serde_json::json!({
        "service": "supaRAG",
        "message": "Rust REST microservice proxy for LightRAG-compatible frontends",
        "health": "/_meta/health"
    }))
}

async fn meta_health(State(state): State<AppState>) -> impl IntoResponse {
    Json(MetaHealth {
        service: "supaRAG",
        status: "ok",
        upstream_url: state.base_url,
    })
}

async fn proxy_any(
    State(state): State<AppState>,
    method: Method,
    uri: Uri,
    headers: HeaderMap,
    body: Bytes,
) -> Response {
    let path = uri.path().to_string();
    let query = uri.query();
    let target_url = build_target_url(&state.base_url, &path, query);

    let mut builder = state.client.request(method.clone(), target_url);
    builder = copy_request_headers(builder, &headers, &state);

    if method != Method::GET && method != Method::HEAD {
        builder = builder.body(body.to_vec());
    }

    match builder.send().await {
        Ok(upstream_res) => {
            if path == "/query/stream" {
                response_from_upstream_stream(upstream_res).await
            } else {
                response_from_upstream(upstream_res).await
            }
        }
        Err(err) => {
            error!("upstream request failed: {}", err);
            error_response(
                StatusCode::BAD_GATEWAY,
                format!("Upstream request failed: {}", err),
            )
        }
    }
}

fn build_target_url(base: &str, path: &str, query: Option<&str>) -> String {
    let mut url = format!("{}{}", base.trim_end_matches('/'), path);
    if let Some(q) = query {
        if !q.is_empty() {
            url.push('?');
            url.push_str(q);
        }
    }
    url
}

fn copy_request_headers(
    mut builder: reqwest::RequestBuilder,
    incoming: &HeaderMap,
    state: &AppState,
) -> reqwest::RequestBuilder {
    for (name, value) in incoming {
        if is_hop_by_hop_header(name) || *name == header::HOST || *name == header::CONTENT_LENGTH {
            continue;
        }
        builder = builder.header(name, value);
    }

    if !incoming.contains_key("x-api-key") {
        if let Some(api_key) = &state.default_api_key {
            builder = builder.header("x-api-key", api_key);
        }
    }

    if !incoming.contains_key(header::AUTHORIZATION) {
        if let Some(token) = &state.default_bearer_token {
            builder = builder.header(header::AUTHORIZATION, format!("Bearer {}", token));
        }
    }

    builder
}

fn copy_response_headers(from: &HeaderMap, to: &mut HeaderMap) {
    for (name, value) in from {
        if is_hop_by_hop_header(name) || *name == header::CONTENT_LENGTH {
            continue;
        }
        to.insert(name, value.clone());
    }
}

fn is_hop_by_hop_header(name: &header::HeaderName) -> bool {
    matches!(
        name.as_str().to_ascii_lowercase().as_str(),
        "connection"
            | "keep-alive"
            | "proxy-authenticate"
            | "proxy-authorization"
            | "te"
            | "trailer"
            | "transfer-encoding"
            | "upgrade"
    )
}

async fn response_from_upstream(upstream_res: reqwest::Response) -> Response {
    let status = upstream_res.status();
    let headers = upstream_res.headers().clone();

    match upstream_res.bytes().await {
        Ok(bytes) => {
            let mut response = Response::new(Body::from(bytes));
            *response.status_mut() = status;
            copy_response_headers(&headers, response.headers_mut());
            response
        }
        Err(err) => {
            error!("failed to read upstream response body: {}", err);
            error_response(
                StatusCode::BAD_GATEWAY,
                format!("Failed to read upstream response body: {}", err),
            )
        }
    }
}

async fn response_from_upstream_stream(upstream_res: reqwest::Response) -> Response {
    let status = upstream_res.status();
    let headers = upstream_res.headers().clone();

    let stream = upstream_res
        .bytes_stream()
        .map(|chunk| chunk.map_err(std::io::Error::other));

    let mut response = Response::new(Body::from_stream(stream));
    *response.status_mut() = status;
    copy_response_headers(&headers, response.headers_mut());
    response
}

fn error_response(status: StatusCode, message: String) -> Response {
    (status, Json(ErrorBody { error: message })).into_response()
}

