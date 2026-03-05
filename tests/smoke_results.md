# supaRAG Smoke Results

- **base_url**: `http://127.0.0.1:8080`
- **selected_cases**: 12
- **passed_transport**: 12
- **failed_transport**: 0

| scenario_id | method | endpoint | family | status_code | ok_transport | note |
|---|---|---|---|---:|---|---|
| SCN-000001 | GET | `/health` | health | 200 | True | ok |
| SCN-000017 | GET | `/auth-status` | auth | 200 | True | ok |
| SCN-000033 | POST | `/login` | auth | 200 | True | ok |
| SCN-000193 | GET | `/graphs` | graph | 422 | True | ok |
| SCN-000273 | POST | `/graph/entity/edit` | graph_mutation | 400 | True | ok |
| SCN-000593 | GET | `/documents` | documents | 200 | True | ok |
| SCN-000625 | POST | `/documents/scan` | documents_mutation | 200 | True | ok |
| SCN-000945 | POST | `/documents/paginated` | documents | 422 | True | ok |
| SCN-001617 | POST | `/documents/upload` | documents_upload | 200 | True | ok |
| SCN-001697 | DELETE | `/documents` | documents_mutation | 200 | True | ok |
| SCN-002193 | POST | `/query` | query | 200 | True | ok |
| SCN-003159 | POST | `/query/stream` | query_stream | 200 | True | ok |
