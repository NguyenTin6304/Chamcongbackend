# Plan.md — Backup Database Tự Động + Quy Trình Restore

> Dự án: Chấm công GPIT — Backend PostgreSQL  
> Mục tiêu: Backup tự động hàng ngày, restore đã test thực tế, không mất dữ liệu

---

## Tổng quan

```
[Cron hàng ngày]
    → pg_dump → file .sql.gz
    → lưu local /backups/ (giữ 7 ngày)
    → copy ra ngoài server (GDrive / SFTP)

[Khi cần restore]
    → tải file .sql.gz
    → chạy restore script
    → verify data
```

---

## Phase 1 — Backup Script

- [ ] Tạo thư mục lưu backup trên server: `/backups/chamcong/`
- [ ] Viết script `scripts/backup_db.sh`:
  - Đọc DB credentials từ `.env` (không hardcode)
  - Chạy `pg_dump` output ra file `chamcong_YYYY-MM-DD_HHmm.sql.gz`
  - Lưu vào `/backups/chamcong/`
  - Tự xóa file cũ hơn 7 ngày
  - Ghi log kết quả (thành công / lỗi + kích thước file)
- [ ] Test script chạy thủ công lần đầu — xác nhận file `.sql.gz` được tạo và có kích thước > 0

---

## Phase 2 — Restore Script

- [ ] Viết script `scripts/restore_db.sh`:
  - Nhận tham số: đường dẫn file `.sql.gz` cần restore
  - Tạo DB backup ngay trước khi restore (safety net)
  - Drop + recreate DB hoặc dùng `--clean` flag
  - Chạy `psql` để restore từ file
  - In kết quả rõ ràng: thành công / lỗi
- [ ] **Test restore thực tế** vào DB tên `chamcong_restore_test` (không đụng production):
  - Tạo DB test
  - Restore file backup vừa tạo ở Phase 1 vào DB test
  - Chạy query đếm số record các bảng chính: `employees`, `attendance_logs`, `attendance_exceptions`
  - Xác nhận số record khớp với production
  - Xóa DB test sau khi verify xong

---

## Phase 3 — Tự Động Hóa (Cron)

- [ ] Cấp quyền execute cho script: `chmod +x scripts/backup_db.sh`
- [ ] Thêm cron job chạy **2:00 sáng mỗi ngày**:
  ```
  0 2 * * * /path/to/chamcongapp/scripts/backup_db.sh >> /var/log/chamcong_backup.log 2>&1
  ```
- [ ] Chờ qua đêm, kiểm tra file backup mới được tạo đúng giờ
- [ ] Kiểm tra log `/var/log/chamcong_backup.log` không có lỗi

---

## Phase 4 — Lưu Ra Ngoài Server

- [ ] Chọn nơi lưu ngoài: Google Drive (dùng `rclone`) hoặc SFTP sang máy khác
- [ ] Cài đặt và cấu hình `rclone` với tài khoản GDrive công ty
- [ ] Thêm vào `backup_db.sh`: sau khi backup xong thì upload lên GDrive
- [ ] Test upload thủ công — xác nhận file xuất hiện trên GDrive
- [ ] Verify có thể download lại file từ GDrive và restore thành công

---

## Phase 5 — Test End-to-End (Quan Trọng Nhất)

- [ ] **Giả lập sự cố**: tạo DB mới rỗng `chamcong_disaster_test`
- [ ] Chạy restore từ file backup trên GDrive (download → restore → verify)
- [ ] Xác nhận dữ liệu đầy đủ: employees, logs, exceptions, leaves, holidays
- [ ] Đo thời gian restore từ đầu đến cuối (để biết downtime thực tế nếu xảy ra)
- [ ] Xóa DB test

---

## Phase 6 — Cập Nhật Tài Liệu

- [ ] Thêm mục "Restore khi sự cố" vào `DEPLOY.md`:
  - Lấy file backup mới nhất từ đâu
  - Chạy lệnh nào
  - Verify sau restore như thế nào
- [ ] Ghi lại kết quả test restore: ngày test, file backup dùng, thời gian, số record verify

---

## Checklist Scripts Cần Tạo

| File | Mô tả | Status |
|---|---|---|
| `scripts/backup_db.sh` | pg_dump + compress + retention | [ ] |
| `scripts/restore_db.sh` | Restore từ file .sql.gz | [ ] |
| `scripts/verify_restore.sh` | Query đếm record sau restore | [ ] |

---

## Định Nghĩa "Xong"

Kế hoạch này hoàn thành khi:
1. Cron tự động chạy mỗi ngày lúc 2h sáng ✓
2. File backup được lưu cả local lẫn GDrive ✓
3. Đã test restore thật từ file GDrive về DB test và verify data khớp ✓
4. DEPLOY.md có hướng dẫn restore đầy đủ ✓
