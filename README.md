# منصة إدارة الصيدليات

منصة لإدارة الصيدلية. اللي جاهز دلوقتي: فواتير الشراء، والمخزون بالتشغيلات، ونقل البيانات من البرامج التانية، والجرد، واقتراح الطلبية، وتنبيهات سحب هيئة الدواء، والكاشير، والبنية التحتية للسلامة الدوائية.

**ابدأ بقراءة [`docs/PROJECT.md`](docs/PROJECT.md)**، فيها الرؤية والحالة والقرارات والخطوة الجاية.

## التشغيل على جهاز الصيدلية (Windows أو Linux)
محتاج Python 3.11 أو أحدث، والمكتبات دي: `starlette` و`uvicorn` و`python-multipart` و`openpyxl` و`pdfplumber` و`pillow` و`requests`.

```bash
pip install -r requirements.txt
python manage.py init --pharmacy "اسم الصيدلية" --username owner      # هيطلب منك كلمة المرور
python manage.py serve                                               # افتح http://127.0.0.1:8000
python manage.py backup                                              # نسخة احتياطية (ينفع تتعمل والبرنامج شغال)
python manage.py verify                                              # يتأكد إن الأرصدة مطابقة لدفتر المخزون
```

قراءة **صور** الفواتير اختيارية، ومحتاجة مفتاح API ومحتاجة تفعيلها للصيدلية:
```bash
set ANTHROPIC_API_KEY=...          # على Linux: export
python manage.py feature <ORG_ID> ai_extraction --monthly 300
```
من غير المفتاح، استيراد ملفات Excel وCSV وPDF (النصية) شغال عادي، والصور بتطلع رسالة واضحة إن الخدمة مش مفعلة.

## الاختبارات
```bash
python -m unittest discover -s tests -t .
```

## الوثائق
| الملف | المحتوى |
|---|---|
| `docs/PROJECT.md` | الوثيقة المركزية (ابدأ منها) |
| `docs/ARCHITECTURE.md` | المعمارية، قاعدة البيانات، الأجهزة الطرفية، التكاملات |
| `docs/DECISIONS.md` | القرارات التقنية وأسبابها (ADR) |
| `docs/ROADMAP.md` | ترتيب المراحل ومعايير القبول |
| `docs/PROGRESS.md` | المكتمل، والاختبارات، والمشكلات المعروفة |
| `docs/SECURITY.md` | الأمان، وإيه اللي اتنفذ، وإيه الناقص |
| `docs/API.md` | واجهات API (لتطبيق الموبايل والتكاملات) |
| `docs/CLINICAL_DATA_SOURCES.md` | ورقة قرار مصادر البيانات الطبية |
| `docs/specs/` | المواصفات الأصلية زي ما اتبعتت |
