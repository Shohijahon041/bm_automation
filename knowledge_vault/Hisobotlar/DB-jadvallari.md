<!-- avto-yaratilgan: db-jadvallari -->
# DB jadvallari

_Bu qayd avtomatik yangilanadi (`knowledge_grow`) — qo'lda tahrir keyingi kengaytirishda ustidan yoziladi._

_yangilandi: 2026-10-27 01:48_

Jami **29** jadval.

- Ko'p korxonali (`company_id`) jadvallar: 23

## `companies` (16 ustun)

- `name` TEXT NOT NULL DEFAULT ''
- `slug` TEXT NOT NULL DEFAULT ''
- `logo` TEXT NOT NULL DEFAULT ''
- `phone` TEXT NOT NULL DEFAULT ''
- `email` TEXT NOT NULL DEFAULT ''
- `address` TEXT NOT NULL DEFAULT ''
- `subscription_plan` TEXT NOT NULL DEFAULT 'basic'
- `subscription_expire_date` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT 'active'
- `max_users` INTEGER NOT NULL DEFAULT 5
- `max_drivers` INTEGER NOT NULL DEFAULT 50
- `max_routes` INTEGER NOT NULL DEFAULT 10
- `features` TEXT NOT NULL DEFAULT '{}'
- `contact_telegram` TEXT NOT NULL DEFAULT ''
- `contact_phone` TEXT NOT NULL DEFAULT ''
- `notes` TEXT NOT NULL DEFAULT ''

## `profiles` (11 ustun) · `company_id`

- `external_id` TEXT NOT NULL DEFAULT ''
- `name` TEXT NOT NULL DEFAULT ''
- `route_id` TEXT NOT NULL DEFAULT ''
- `profile_id` TEXT NOT NULL DEFAULT ''
- `route_name` TEXT NOT NULL DEFAULT ''
- `start1` TEXT NOT NULL DEFAULT ''
- `start2` TEXT NOT NULL DEFAULT ''
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `routes` (9 ustun) · `company_id`

- `external_id` TEXT NOT NULL
- `name` TEXT NOT NULL DEFAULT ''
- `parent_id` TEXT NOT NULL DEFAULT ''
- `entity_type` TEXT NOT NULL DEFAULT ''
- `is_brutto` TEXT NOT NULL DEFAULT 't'
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `vehicles` (9 ustun) · `company_id`

- `external_id` TEXT NOT NULL
- `plate_number` TEXT NOT NULL DEFAULT ''
- `garage_number` TEXT NOT NULL DEFAULT ''
- `model` TEXT NOT NULL DEFAULT ''
- `route_id` TEXT NOT NULL DEFAULT ''
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `drivers` (8 ustun) · `company_id`

- `external_id` TEXT NOT NULL
- `full_name` TEXT NOT NULL DEFAULT ''
- `tin` TEXT NOT NULL DEFAULT ''
- `route_id` TEXT NOT NULL DEFAULT ''
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `driver_profiles` (26 ustun) · `company_id`

- `driver_id` TEXT NOT NULL
- `phone` TEXT NOT NULL DEFAULT ''
- `passport_number` TEXT NOT NULL DEFAULT ''
- `passport_issued_by` TEXT NOT NULL DEFAULT ''
- `passport_expiry` TEXT NOT NULL DEFAULT ''
- `license_number` TEXT NOT NULL DEFAULT ''
- `license_category` TEXT NOT NULL DEFAULT ''
- `license_expiry` TEXT NOT NULL DEFAULT ''
- `gender` TEXT NOT NULL DEFAULT ''
- `passport_front_path` TEXT NOT NULL DEFAULT ''
- `passport_back_path` TEXT NOT NULL DEFAULT ''
- `license_front_path` TEXT NOT NULL DEFAULT ''
- `license_back_path` TEXT NOT NULL DEFAULT ''
- `photo_path` TEXT NOT NULL DEFAULT ''
- `rating` REAL NOT NULL DEFAULT 5
- `blacklisted` INTEGER NOT NULL DEFAULT 0
- `blacklist_reason` TEXT NOT NULL DEFAULT ''
- `km_rate` REAL NOT NULL DEFAULT 0
- `notification_enabled` INTEGER NOT NULL DEFAULT 0
- `notification_target` TEXT NOT NULL DEFAULT ''
- `telegram_chat_id` TEXT NOT NULL DEFAULT ''
- `notes` TEXT NOT NULL DEFAULT ''
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `driver_work_logs` (14 ustun) · `company_id`

- `date` TEXT NOT NULL
- `driver_id` TEXT NOT NULL
- `vehicle_id` TEXT NOT NULL DEFAULT ''
- `distance_km` REAL NOT NULL DEFAULT 0
- `trip_count` INTEGER NOT NULL DEFAULT 0
- `note` TEXT NOT NULL DEFAULT ''
- `distance_plan` REAL NOT NULL DEFAULT 0
- `trip_plan` INTEGER NOT NULL DEFAULT 0
- `working_day` INTEGER NOT NULL DEFAULT 0
- `trip_passed` INTEGER NOT NULL DEFAULT 0
- `trip_approved` INTEGER NOT NULL DEFAULT 0
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `driver_fines` (8 ustun) · `company_id`

- `driver_id` TEXT NOT NULL
- `date` TEXT NOT NULL
- `amount` REAL NOT NULL DEFAULT 0
- `reason` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT 'ACTIVE'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `duties` (8 ustun) · `company_id`

- `external_id` TEXT NOT NULL
- `date` TEXT NOT NULL
- `route_id` TEXT NOT NULL DEFAULT ''
- `shift_id` TEXT NOT NULL DEFAULT ''
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `schedules` (13 ustun) · `company_id`

- `date` TEXT NOT NULL
- `route_id` TEXT NOT NULL DEFAULT ''
- `graph_name` TEXT NOT NULL DEFAULT ''
- `driver_id` TEXT NOT NULL DEFAULT ''
- `vehicle_id` TEXT NOT NULL DEFAULT ''
- `shift_name` TEXT NOT NULL DEFAULT ''
- `start_time` TEXT NOT NULL DEFAULT ''
- `end_time` TEXT NOT NULL DEFAULT ''
- `trip_count` INTEGER NOT NULL DEFAULT 0
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `waybills` (11 ustun) · `company_id`

- `date` TEXT NOT NULL
- `route_id` TEXT NOT NULL DEFAULT ''
- `vehicle_id` TEXT NOT NULL DEFAULT ''
- `driver_id` TEXT NOT NULL DEFAULT ''
- `plate_number` TEXT NOT NULL DEFAULT ''
- `direction` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT ''
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `trips` (14 ustun) · `company_id`

- `date` TEXT NOT NULL
- `route_id` TEXT NOT NULL DEFAULT ''
- `vehicle_id` TEXT NOT NULL DEFAULT ''
- `driver_id` TEXT NOT NULL DEFAULT ''
- `planned_time` TEXT NOT NULL DEFAULT ''
- `actual_time` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT 'PENDING_ACCESS'
- `source` TEXT NOT NULL DEFAULT 'DUTY'
- `last_synced_at` TEXT NOT NULL DEFAULT ''
- `distance_km` REAL NOT NULL DEFAULT 0
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `route_daily` (19 ustun) · `company_id`

- `date` TEXT NOT NULL
- `route_id` TEXT NOT NULL DEFAULT ''
- `vehicle_id` TEXT NOT NULL DEFAULT ''
- `vehicle_number` TEXT NOT NULL DEFAULT ''
- `vehicle_brand` TEXT NOT NULL DEFAULT ''
- `shift_name` TEXT NOT NULL DEFAULT ''
- `working_day` INTEGER NOT NULL DEFAULT 0
- `trip_plan` INTEGER NOT NULL DEFAULT 0
- `trip_fact` INTEGER NOT NULL DEFAULT 0
- `trip_passed` INTEGER NOT NULL DEFAULT 0
- `trip_approved` INTEGER NOT NULL DEFAULT 0
- `distance_plan` REAL NOT NULL DEFAULT 0
- `distance_fact` REAL NOT NULL DEFAULT 0
- `distance_fact_extra` REAL NOT NULL DEFAULT 0
- `last_synced_at` TEXT NOT NULL DEFAULT ''
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `trip_statuses` (3 ustun)

- `code` TEXT NOT NULL
- `name` TEXT NOT NULL DEFAULT ''
- `description` TEXT NOT NULL DEFAULT ''

## `reports` (10 ustun) · `company_id`

- `name` TEXT NOT NULL
- `report_type` TEXT NOT NULL DEFAULT ''
- `period_date` TEXT NOT NULL DEFAULT ''
- `params` TEXT NOT NULL DEFAULT '{}'
- `params_hash` TEXT NOT NULL DEFAULT ''
- `file_path` TEXT NOT NULL DEFAULT ''
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `report_runs` (7 ustun)

- `run_id` TEXT NOT NULL DEFAULT ''
- `report_name` TEXT NOT NULL DEFAULT ''
- `started_at` TEXT NOT NULL DEFAULT ''
- `finished_at` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT ''
- `error` TEXT NOT NULL DEFAULT ''
- `output_file` TEXT NOT NULL DEFAULT ''

## `errors` (5 ustun)

- `source` TEXT NOT NULL DEFAULT ''
- `message` TEXT NOT NULL DEFAULT ''
- `traceback` TEXT NOT NULL DEFAULT ''
- `context` TEXT NOT NULL DEFAULT '{}'
- `occurred_at` TEXT NOT NULL DEFAULT ''

## `notifications` (5 ustun)

- `channel` TEXT NOT NULL DEFAULT ''
- `target` TEXT NOT NULL DEFAULT ''
- `message` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT ''
- `sent_at` TEXT NOT NULL DEFAULT ''

## `automation_runs` (8 ustun)

- `run_id` TEXT NOT NULL
- `trigger` TEXT NOT NULL DEFAULT ''
- `started_at` TEXT NOT NULL DEFAULT ''
- `finished_at` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT ''
- `summary` TEXT NOT NULL DEFAULT '{}'
- `sheet_date` TEXT NOT NULL DEFAULT ''
- `month` TEXT NOT NULL DEFAULT ''

## `dispatcher_routes` (9 ustun) · `company_id`

- `dispatcher_chat_id` TEXT NOT NULL
- `route_id` TEXT NOT NULL
- `route_name` TEXT NOT NULL DEFAULT ''
- `company` TEXT NOT NULL DEFAULT ''
- `phone` TEXT NOT NULL DEFAULT ''
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `documents` (12 ustun) · `company_id`

- `title` TEXT NOT NULL DEFAULT ''
- `template_key` TEXT NOT NULL DEFAULT ''
- `category` TEXT NOT NULL DEFAULT ''
- `driver_id` TEXT NOT NULL DEFAULT ''
- `fields` TEXT NOT NULL DEFAULT '{}'
- `body_text` TEXT NOT NULL DEFAULT ''
- `body_html` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT 'draft'
- `attachments` TEXT NOT NULL DEFAULT '[]'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `sms_log` (14 ustun) · `company_id`

- `driver_id` TEXT NOT NULL DEFAULT ''
- `name` TEXT NOT NULL DEFAULT ''
- `phone` TEXT NOT NULL DEFAULT ''
- `route_id` TEXT NOT NULL DEFAULT ''
- `route_name` TEXT NOT NULL DEFAULT ''
- `schedule_date` TEXT NOT NULL DEFAULT ''
- `message` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT 'PENDING'
- `message_id` TEXT NOT NULL DEFAULT ''
- `error` TEXT NOT NULL DEFAULT ''
- `send_at` TEXT NOT NULL DEFAULT ''
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `sms_route_flags` (5 ustun) · `company_id`

- `route_id` TEXT NOT NULL
- `enabled` INTEGER NOT NULL DEFAULT 1
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `avans` (10 ustun) · `company_id`

- `driver_id` TEXT NOT NULL
- `name` TEXT NOT NULL DEFAULT ''
- `route_id` TEXT NOT NULL DEFAULT ''
- `route_name` TEXT NOT NULL DEFAULT ''
- `amount` REAL NOT NULL DEFAULT 0
- `pay_date` TEXT NOT NULL DEFAULT ''
- `note` TEXT NOT NULL DEFAULT ''
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `staff` (10 ustun) · `company_id`

- `name` TEXT NOT NULL
- `position` TEXT NOT NULL DEFAULT ''
- `company` TEXT NOT NULL DEFAULT ''
- `salary_type` TEXT NOT NULL DEFAULT 'oylik'
- `rate` REAL NOT NULL DEFAULT 0
- `days` INTEGER NOT NULL DEFAULT 0
- `note` TEXT NOT NULL DEFAULT ''
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `driver_appeals` (9 ustun) · `company_id`

- `driver_id` TEXT NOT NULL
- `title` TEXT NOT NULL DEFAULT ''
- `text` TEXT NOT NULL DEFAULT ''
- `status` TEXT NOT NULL DEFAULT 'YANGI'
- `reply` TEXT NOT NULL DEFAULT ''
- `replied_at` TEXT NOT NULL DEFAULT ''
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `dashboard_users` (10 ustun) · `company_id`

- `username` TEXT NOT NULL
- `salt` TEXT NOT NULL DEFAULT ''
- `password_hash` TEXT NOT NULL DEFAULT ''
- `role` TEXT NOT NULL DEFAULT 'VIEWER'
- `company` TEXT NOT NULL DEFAULT ''
- `active` INTEGER NOT NULL DEFAULT 1
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `video_cameras` (9 ustun) · `company_id`

- `name` TEXT NOT NULL
- `source_type` TEXT NOT NULL DEFAULT 'hls'
- `url` TEXT NOT NULL
- `vehicle_id` TEXT NOT NULL DEFAULT ''
- `enabled` INTEGER NOT NULL DEFAULT 1
- `data` TEXT NOT NULL DEFAULT '{}'
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

## `video_audit` (9 ustun) · `company_id`

- `camera_id` BIGINT NOT NULL
- `user_id` BIGINT NOT NULL
- `username` TEXT NOT NULL
- `action` TEXT NOT NULL
- `ip` TEXT NOT NULL DEFAULT ''
- `duration_s` INTEGER NOT NULL DEFAULT 0
- `company_id` BIGINT NOT NULL DEFAULT 0
- `created_by` TEXT NOT NULL DEFAULT ''
- `updated_by` TEXT NOT NULL DEFAULT ''

<!-- avto-bog'liq:start -->
## Bog'liq qaydlar

- [[Bilim-bazasi]] — bosh indeks
- [[Muhit-va-konfiguratsiya]]
- [[Bot-sozlamalari]]

<!-- avto-bog'liq:end -->
