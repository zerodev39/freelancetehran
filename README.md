# Freelance Tehran — نسخه ماژولار

## اجرا
1. Python 3 را نصب داشته باشید.
2. فایل ZIP را کامل Extract کنید.
3. وارد پوشه `freelance_tehran_project` شوید.
4. اجرا کنید:

```bash
python server.py
```

5. در مرورگر باز کنید: http://127.0.0.1:8000

## ساختار
- `server.py`: سرور، APIها و منطق پایگاه داده
- `templates/index.html`: قالب اصلی صفحه
- `templates/home.html`: هدر و بخش‌های ابتدایی خانه
- `templates/projects.html`: بخش پروژه‌ها
- `templates/home_end.html`: ادامه صفحه اصلی و راهنمای همکاری
- `templates/dashboard.html`: پنل کاربری
- `templates/footer.html`: فوتر
- `templates/modals.html`: پنجره‌های ورود، ثبت‌نام و فرم‌ها
- `static/css/style.css`: تمام استایل‌ها و ریسپانسیو
- `static/js/app.js`: کدهای سمت کاربر و ارتباط با API
- `freelance_cube.db`: هنگام اولین اجرا به‌صورت خودکار ساخته می‌شود.

این پروژه از کتابخانه‌های استاندارد پایتون استفاده می‌کند و نصب پکیج اضافی لازم ندارد. فونت وزیرمتن از Google Fonts بارگذاری می‌شود و برای دریافت آن اتصال اینترنت لازم است.
