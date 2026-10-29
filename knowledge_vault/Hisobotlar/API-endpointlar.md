<!-- avto-yaratilgan: api-endpointlar -->
# API endpointlar

_Bu qayd avtomatik yangilanadi (`knowledge_grow`) — qo'lda tahrir keyingi kengaytirishda ustidan yoziladi._

_yangilandi: 2026-10-27 01:48_

Jami **143** endpoint, 7 modulda.

## `bm_automation/app/dashboard/routers/admin.py`

- `DELETE` `/api/admin/companies/{company_id}`
- `DELETE` `/api/video/cameras/{cam_id}`
- `GET   ` `/api/admin/avans`
- `GET   ` `/api/admin/companies/{company_id}/users`
- `GET   ` `/api/admin/companies/{company_id}`
- `GET   ` `/api/admin/companies`
- `GET   ` `/api/admin/download-all`
- `GET   ` `/api/admin/download`
- `GET   ` `/api/admin/drivers`
- `GET   ` `/api/admin/fines`
- `GET   ` `/api/admin/staff/download`
- `GET   ` `/api/admin/staff`
- `GET   ` `/api/admin/summary`
- `GET   ` `/api/dispatchers`
- `GET   ` `/api/documents/autofill`
- `GET   ` `/api/documents/templates`
- `GET   ` `/api/documents/{doc_id}/html`
- `GET   ` `/api/documents/{doc_id}`
- `GET   ` `/api/documents`
- `GET   ` `/api/group-stats`
- `GET   ` `/api/settings/companies`
- `GET   ` `/api/settings/env`
- `GET   ` `/api/settings`
- `GET   ` `/api/sms-log`
- `GET   ` `/api/sms/routes`
- `GET   ` `/api/users/{uid}/driver-info`
- `GET   ` `/api/users/{uid}/photo`
- `GET   ` `/api/users/{uid}`
- `GET   ` `/api/users`
- `GET   ` `/api/video/archive/range`
- `GET   ` `/api/video/cameras/{cam_id}/token`
- `GET   ` `/api/video/cameras`
- `GET   ` `/api/video/channels`
- `GET   ` `/api/video/grid`
- `PATCH ` `/api/video/cameras/{cam_id}`
- `POST  ` `/api/admin/avans/delete`
- `POST  ` `/api/admin/avans`
- `POST  ` `/api/admin/companies/detect`
- `POST  ` `/api/admin/companies/edit`
- `POST  ` `/api/admin/companies/onboard`
- `POST  ` `/api/admin/companies/{company_id}/activate`
- `POST  ` `/api/admin/companies/{company_id}/users`
- `POST  ` `/api/admin/companies`
- `POST  ` `/api/admin/fines/delete`
- `POST  ` `/api/admin/staff/delete`
- `POST  ` `/api/admin/staff`
- `POST  ` `/api/dispatchers/{uid}/routes`
- `POST  ` `/api/documents/ai-generate`
- `POST  ` `/api/documents/{doc_id}/attach`
- `POST  ` `/api/documents/{doc_id}/delete`
- `POST  ` `/api/documents/{doc_id}/detach`
- `POST  ` `/api/documents/{doc_id}`
- `POST  ` `/api/documents`
- `POST  ` `/api/settings/companies`
- `POST  ` `/api/settings/env`
- `POST  ` `/api/settings`
- `POST  ` `/api/sms/retry`
- `POST  ` `/api/sms/routes`
- `POST  ` `/api/sms/send`
- `POST  ` `/api/users/{uid}/delete`
- `POST  ` `/api/users/{uid}/link-driver`
- `POST  ` `/api/users/{uid}/pages`
- `POST  ` `/api/users/{uid}/role`
- `POST  ` `/api/video/cameras`
- `POST  ` `/api/video/token/refresh`
- `PUT   ` `/api/admin/companies/{company_id}`

## `bm_automation/app/dashboard/routers/auth.py`

- `GET   ` `/api/dashboard-users`
- `GET   ` `/api/me`
- `POST  ` `/api/change-password`
- `POST  ` `/api/dashboard-users`
- `POST  ` `/api/login`
- `POST  ` `/api/logout`

## `bm_automation/app/dashboard/routers/data.py`

- `GET   ` `/api/appeals`
- `GET   ` `/api/attendance`
- `GET   ` `/api/brutto/master`
- `GET   ` `/api/brutto`
- `GET   ` `/api/check`
- `GET   ` `/api/electricity/price`
- `GET   ` `/api/electricity`
- `GET   ` `/api/export`
- `GET   ` `/api/kmrate`
- `GET   ` `/api/month`
- `GET   ` `/api/rejects/tariff`
- `GET   ` `/api/rejects`
- `GET   ` `/api/route-skm`
- `GET   ` `/api/routes`
- `GET   ` `/api/salary`
- `GET   ` `/api/summary`
- `POST  ` `/api/appeals`
- `POST  ` `/api/electricity/price`
- `POST  ` `/api/kmrate`
- `POST  ` `/api/rejects/tariff`
- `POST  ` `/api/route-skm`

## `bm_automation/app/dashboard/routers/discovery_admin.py`

- `GET   ` `/api/admin/discovery/status`
- `POST  ` `/api/admin/discovery/import`

## `bm_automation/app/dashboard/routers/drivers.py`

- `GET   ` `/api/drivers/list`
- `GET   ` `/api/drivers/{driver_id}/document/{kind}`
- `GET   ` `/api/drivers/{driver_id}/photo`
- `GET   ` `/api/drivers/{driver_id}`
- `GET   ` `/api/drivers`
- `GET   ` `/api/vehicles/{vehicle_id}`
- `GET   ` `/api/work-logs`
- `POST  ` `/api/drivers/bm-sync`
- `POST  ` `/api/drivers/{driver_id}/bm-sync`
- `POST  ` `/api/drivers/{driver_id}/document`
- `POST  ` `/api/drivers/{driver_id}/fine`
- `POST  ` `/api/drivers/{driver_id}/notification-test`
- `POST  ` `/api/drivers/{driver_id}/photo`
- `POST  ` `/api/drivers/{driver_id}/profile`
- `POST  ` `/api/drivers/{driver_id}/work-log-delete`
- `POST  ` `/api/drivers/{driver_id}/work-log`
- `POST  ` `/api/drivers`

## `bm_automation/app/dashboard/routers/knowledge.py`

- `GET   ` `/api/knowledge/graph`
- `GET   ` `/api/knowledge/grow`
- `GET   ` `/api/knowledge/list`
- `GET   ` `/api/knowledge/note`
- `GET   ` `/api/knowledge/search`
- `GET   ` `/api/knowledge/stats`
- `POST  ` `/api/knowledge/delete`
- `POST  ` `/api/knowledge/grow`
- `POST  ` `/api/knowledge/note`

## `bm_automation/app/dashboard/routers/system.py`

- `GET   ` `/api/backup/run`
- `GET   ` `/api/backup/status`
- `GET   ` `/api/disk`
- `GET   ` `/api/health`
- `GET   ` `/api/insights`
- `GET   ` `/api/logs/stream`
- `GET   ` `/api/logs`
- `GET   ` `/api/myai/activity`
- `GET   ` `/api/myai/events`
- `GET   ` `/api/myai/registry`
- `GET   ` `/api/myai/status`
- `GET   ` `/api/myai/task/{task_id}/logs`
- `GET   ` `/api/myai/task/{task_id}`
- `GET   ` `/api/myai/tasks`
- `GET   ` `/api/sync/monthly/status`
- `GET   ` `/api/sync/status`
- `POST  ` `/api/myai/task/cancel`
- `POST  ` `/api/myai/task/pause`
- `POST  ` `/api/myai/task/resume`
- `POST  ` `/api/myai/task`
- `POST  ` `/api/sync/monthly`
- `POST  ` `/api/sync`

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Bot-buyruqlari]]
- [[Bilim-bazasini-yuritish]]
- [[GPS-kuzatuv]]
- [[Log-tahlili]]

<!-- avto-bog'liq:end -->
