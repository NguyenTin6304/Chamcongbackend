# Chấm Công App (FastAPI + PostgreSQL)

## 1) Tổng quan
Backend cham cong theo GPS (geofence), co 2 nhom nguoi dung:
- `USER`: check-in/check-out, xem lich su cua minh.
- `ADMIN`: cau hinh rule vi tri, quan ly employee, xem report, export Excel.

Trang thai hien tai: backend da san sang cho Flutter User MVP.

## 2) Stack công nghệ
- FastAPI
- SQLAlchemy ORM
- PostgreSQL
- Alembic (migration)
- JWT auth (`python-jose`)
- Password hash (`passlib` + `bcrypt`)
- Excel export (`openpyxl`)

## 3) Cau truc thu muc
```text
app/
  api/
    auth.py
    employees.py
    rules.py
    attendance.py
    reports.py
  core/
    config.py
    db.py
    security.py
    deps.py
  schemas/
    auth.py
    employees.py
    rules.py
    attendance.py
  services/
    geo.py
  models.py
  main.py
alembic/
  env.py
  versions/
requirements.txt
README.md
```

## 4) Data model
### users
- `id`, `email` (unique), `password_hash`, `role` (`USER`/`ADMIN`), `created_at`

### employees
- `id`, `code` (unique), `full_name`, `user_id` (FK -> users.id, nullable, unique), `created_at`
- Rule: 1 user chi duoc gan cho 1 employee.

### checkin_rules
- `id`, `latitude`, `longitude`, `radius_m`, `active`, `updated_at`

### attendance_logs
- `id`, `employee_id`, `type` (`IN`/`OUT`), `time`
- `lat`, `lng`, `distance_m`, `is_out_of_range`, `address_text`

## 5) Bao mat va auth
- Bearer JWT (`Authorization: Bearer <token>`)
- JWT config tu `.env`:
  - `SECRET_KEY`
  - `ALGORITHM`
  - `ACCESS_TOKEN_EXPIRE_MINUTES`
- `POST /auth/register` chi tao `USER`.

## 6) Error response format (chuan hoa)
Tat ca loi API tra theo dang:
```json
{
  "success": false,
  "error": {
    "code": "VALIDATION_ERROR",
    "message": "Invalid request data",
    "details": []
  }
}
```

## 7) API contract cho frontend

### Auth
- `POST /auth/register`
  - Request:
  ```json
  { "email": "user@mail.com", "password": "123456" }
  ```
  - Response 201:
  ```json
  { "id": 1, "email": "user@mail.com", "role": "USER" }
  ```

- `POST /auth/login` (endpoint chinh, JSON)
  - Request:
  ```json
  { "email": "user@mail.com", "password": "123456" }
  ```
  - Response 200:
  ```json
  { "access_token": "<jwt>", "token_type": "bearer" }
  ```

- `GET /auth/me` (can token)
  - Response 200:
  ```json
  { "id": 1, "email": "user@mail.com", "role": "USER" }
  ```

- Legacy (deprecated, chi de tuong thich):
  - `POST /auth/login-form` (form-data)
  - `POST /auth/login-json` (alias cu)

### Attendance (User)
- `GET /attendance/status`
  - Muc dich: frontend biet hien nut check-in hay check-out.
  - Response mau:
  ```json
  {
    "employee_assigned": true,
    "employee_id": 10,
    "current_state": "OUT",
    "last_action": "OUT",
    "last_action_time": "2026-03-09T09:30:00+07:00",
    "can_checkin": true,
    "can_checkout": false,
    "message": "Ban dang o trang thai check-out"
  }
  ```

- `POST /attendance/checkin`
  - Request chap nhan ca 2 dang key:
  ```json
  { "lat": 10.7769, "lng": 106.7009 }
  ```
  hoac
  ```json
  { "latitude": 10.7769, "longitude": 106.7009 }
  ```

- `POST /attendance/checkout` (request giong checkin)
- `GET /attendance/me`

### Rules (Admin)
- `GET /rules/active`
- `PUT /rules/active`
  - Chap nhan key: `latitude/longitude/radius_m` hoac `lat/lng/radius`.

### Employees (Admin)
- `POST /employees`
- `GET /employees`
- `GET /employees/{employee_id}`
- `PUT /employees/{employee_id}/assign-user`
- `GET /employees/me` (user)

### Reports (Admin)
- `GET /attendance/report/daily`
- `GET /reports/attendance.xlsx`

## 8) Changelog phien nay (2026-03-09)
Da hoan thanh theo thu tu yeu cau 1 -> 2 -> 3:
1. Chuan hoa auth endpoint:
- Giu `/auth/login` la endpoint chinh cho frontend JSON.
- Them `/auth/me` de frontend lay profile/role hien tai.
- Danh dau endpoint cu `/auth/login-form`, `/auth/login-json` la deprecated.

2. Chot contract API:
- Bo sung section "API contract cho frontend" trong README nay.
- Ghi ro request/response/status cua cac endpoint chinh de Flutter tich hop on dinh.

3. Polish nghiep vu attendance:
- Them `GET /attendance/status` de app biet trang thai IN/OUT va hien dung nut.
- Status tra du lieu co cau truc, ke ca truong hop chua gan employee (`UNASSIGNED`).

## 9) Cau hinh va chay local
### `.env`
```env
DATABASE_URL=postgresql+psycopg2://<user>:<password>@localhost:5432/<dbname>
SECRET_KEY=CHANGE_ME_TO_A_RANDOM_LONG_STRING
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=1440
```

### Lenh chay
```powershell
pip install -r requirements.txt
python -m alembic upgrade head
uvicorn app.main:app --reload
```

Swagger: `http://127.0.0.1:8000/docs`

## 10) Migration notes
- `052e5352dd82_init_schema.py`: tao schema ban dau.
- `86ce84c0b91f_init_tables.py`: revision tiep theo, dang `pass`.
- `b7f95d2e1a31_add_unique_employee_user_id.py`: them unique constraint cho `employees.user_id`.

## 11) Roadmap Flutter (da chot)
1. Flutter User MVP
- Login
- Lay GPS
- Check-in / Check-out
- Lich su `/attendance/me`
- Hien thi distance + trong/ngoai vung + message API

2. Flutter Admin MVP
- Set rule `/rules/active`
- Quan ly employee (list + assign user)
- Export Excel `/reports/attendance.xlsx`

3. Nang cap phan quyen
- `VIEW_EMPLOYEES`, `EXPORT_REPORT`, `VIEW_LOGS`, `MANAGE_RULES`
- UI admin hien theo permission

4. Polish nghiep vu
- Chuan hoa timezone hien thi VN
## 12) Migration + seed + test nhanh
### Kiem tra migration da len revision moi nhat
```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
```
Ky vong: `b7f95d2e1a31 (head)`.

### Seed du lieu dev/test (idempotent)
```powershell
.\.venv\Scripts\python.exe scripts\seed_dev_data.py
```
Script tao/cap nhat:
- 1 admin user (mac dinh `admin@example.com` / `admin123`)
- 1 normal user (mac dinh `user@example.com` / `user123`)
- 2 employee va gan `user_id`
- 1 active checkin rule

Co the truyen tham so tuy chinh, vi du:
```powershell
.\.venv\Scripts\python.exe scripts\seed_dev_data.py --admin-email admin@gpit.vn --rule-radius 300
```

## 13) Bao mat toi thieu
- JWT secret chi doc tu `.env` qua `app/core/config.py`, khong hardcode trong `security.py`.
- `SECRET_KEY` duoc validate toi thieu 16 ky tu.
- `POST /auth/register` chi tao role `USER`.
- Role `ADMIN` cap bang script seed (hoac API admin rieng trong tuong lai).

## 14) Kiem thu toi thieu (4 luong bat buoc)
File test: `tests/test_minimum_flows.py`

Chay test:
```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_minimum_flows -v
```

4 luong da co test:
- `register/login`
- `set rule`
- `checkin/checkout`
- `export report`

## 15) Changelog bo sung (2026-03-09)
- Da xac nhan migration database dang o `head`: `b7f95d2e1a31`.
- Da them script `scripts/seed_dev_data.py` de dev/test nhanh.
- Da bo sung test tu dong cho 4 luong bat buoc.
- Da sua `reports` export de tuong thich du lieu theo nhieu backend (tranh loi `.isoformat()` tren kieu `str` khi test).

## 16) Changelog bo sung (2026-03-10) - Backend
### Migration, seed, security, test
- Da chay `alembic upgrade head` va xac nhan current revision: `b7f95d2e1a31 (head)`.
- Da them script seed idempotent: `scripts/seed_dev_data.py`.
- Da sieu toi thieu bao mat config: `SECRET_KEY` doc tu `.env`, validate min length 16.
- Register van chi tao role `USER`.
- Da them test bat buoc: `tests/test_minimum_flows.py` gom 4 luong:
  - register/login
  - set rule
  - checkin/checkout
  - export report
- Da fix report export de tranh loi khi `work_date` tra ve kieu chuoi tren mot so backend test.

### Auth + Attendance contract
- Chuan hoa auth:
  - `POST /auth/login` la endpoint chinh cho JSON.
  - `GET /auth/me` de lay profile hien tai.
  - `/auth/login-form` va `/auth/login-json` giu lai de tuong thich, danh dau deprecated.
- Da them `GET /attendance/status` de frontend bat/tat dung nut checkin/checkout.
- `LocationRequest` chap nhan ca key `lat/lng` va `latitude/longitude`.

## 17) Changelog bo sung (2026-03-10) - Frontend Flutter User MVP (birdle)
Project frontend: `E:\CongtyGPIT\fluttertest\birdle`

### Da hoan thanh
- Auth UI:
  - Login screen (`/auth/login`)
  - Register screen (`/auth/register`)
  - Luu token local bang `shared_preferences`
- Attendance User MVP:
  - Lay GPS that bang `geolocator`
  - Check-in / Check-out bang toa do GPS hien tai
  - Hien thi ket qua action gan nhat: type, time, distance, range, message
  - Lay va hien thi lich su tu `GET /attendance/me`
  - Dong bo trang thai nut qua `GET /attendance/status`
- UI/UX polish:
  - Loading states ro rang (global + button level)
  - Badge trang thai (`IN/OUT/UNASSIGNED`) va badge range (`Trong vung/Ngoai vung`)
  - Chuan hoa format thoi gian hien thi VN (UTC+7)
- Mobile permissions:
  - Android: `ACCESS_COARSE_LOCATION`, `ACCESS_FINE_LOCATION`
  - iOS: `NSLocationWhenInUseUsageDescription`, `NSLocationAlwaysAndWhenInUseUsageDescription`

### Frontend files chinh da tao/sua
- `lib/core/config/app_config.dart`
- `lib/core/storage/token_storage.dart`
- `lib/features/auth/data/auth_api.dart`
- `lib/features/auth/presentation/login_page.dart`
- `lib/features/auth/presentation/register_page.dart`
- `lib/features/attendance/data/attendance_api.dart`
- `lib/features/home/presentation/home_page.dart`
- `pubspec.yaml`
- `android/app/src/main/AndroidManifest.xml`
- `ios/Runner/Info.plist`

## 18) Group/Geofence rollout (2026-03-11)
### 1. Nghi?p v? d� ch?t v� d� �p d?ng
- Employee thu?c 1 group (`employees.group_id`).
- Group c� nhi?u geofence (`group_geofences`).
- Check-in/out h?p l? n?u n?m trong �t nh?t 1 geofence active c?a group.
- N?u ngo�i t?t c? geofence c?a group => `is_out_of_range=true`.
- Fallback t?m th?i: n?u employee chua c� group ho?c group chua c� geofence active, backend d�ng `rules/active` d? kh�ng v? app cu.

### 2. D? li?u d� th�m
- B?ng `groups`: `id`, `code`, `name`, `active`, `created_at`.
- B?ng `group_geofences`: `group_id`, `name`, `latitude`, `longitude`, `radius_m`, `active`, `created_at`.
- C?t `employees.group_id` (nullable, FK -> `groups.id`).

### 3. Migration + seed
- Migration m?i: `e8b1c2d3f4a5_add_group_geofence_tables.py`.
- �� `alembic upgrade head` th�nh c�ng, current head: `e8b1c2d3f4a5`.
- Seed script c?p nh?t: `scripts/seed_dev_data.py`.
  - 1 admin + 2 user demo.
  - 2 group demo:
    - `Q1_OFFICE` v?i 2 geofence (`Cong chinh`, `Toa nha phu`).
    - `BT_WAREHOUSE` v?i 1 geofence (`Kho trung tam`).
  - Employee du?c g�n group tuong ?ng.
  - V?n t?o active fallback rule cho tuong th�ch ngu?c.

## 19) Group time-rule per group (2026-03-11)
### Muc tieu
- Moi group co the cau hinh gio vao/ra rieng:
  - `start_time` + `grace_minutes` (check-in)
  - `end_time` + `checkout_grace_minutes` (check-out)
- Trang thai `EARLY/ON_TIME/LATE` duoc tinh theo group cua employee.

### Data model cap nhat
- Bang `groups` them 4 cot nullable:
  - `start_time` (`TIME`)
  - `grace_minutes` (`INT`)
  - `end_time` (`TIME`)
  - `checkout_grace_minutes` (`INT`)
- Migration moi: `alembic/versions/9c7e6d5b4a3f_add_group_time_rule_fields.py`

### Uu tien rule khi cham cong
1. Employee co group active:
- Lay geofence theo group de check in-range/out-of-range.
- Lay time-rule theo group neu co.
2. Neu group khong set day du field time-rule:
- Tung field se fallback ve `rules/active`.
3. Employee khong co group (hoac group khong co geofence active):
- Fallback geofence + time-rule ve `rules/active`.

### API groups cap nhat
- `POST /groups` va `PUT /groups/{group_id}` ho tro them:
  - `start_time` (`HH:MM`)
  - `grace_minutes`
  - `end_time` (`HH:MM`)
  - `checkout_grace_minutes`
- `GET /groups` tra ve day du cac field time-rule tren.

### Seed va test cap nhat
- `scripts/seed_dev_data.py` da seed 2 group voi gio vao/ra khac nhau.
- `tests/test_minimum_flows.py` bo sung:
  - `test_group_time_rule_crud_flow`
  - `test_group_time_rule_overrides_system_rule`

## 20) Changelog bo sung (2026-03-12) - Group fallback observability
- Bo sung truong theo doi geofence source/fallback trong `attendance_logs`:
  - `geofence_source` (`GROUP` / `SYSTEM_FALLBACK`)
  - `fallback_reason` (nullable)
- API attendance cap nhat:
  - `AttendanceLogResponse` them `geofence_source`, `fallback_reason`
  - Daily report `/attendance/report/daily` them `geofence_source`, `fallback_reason`
- Excel report `/reports/attendance.xlsx` them 2 cot moi:
  - `geofence_source`
  - `fallback_reason`
- Migration moi: `ab12cd34ef56_add_attendance_geofence_source_fields.py`
- Da chay migration thanh cong, current head: `ab12cd34ef56`.
- Test cap nhat:
  - Them `test_daily_report_contains_fallback_source`
  - Mo rong `test_export_report_flow` de assert cot/du lieu fallback
  - Tong so test: 12, da pass toan bo.
