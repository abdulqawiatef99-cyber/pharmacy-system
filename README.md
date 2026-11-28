# منصة إدارة الصيدليات

منصة لإدارة الصيدلية. اللي جاهز دلوقتي: فواتير الشراء، والمخزون بالتشغيلات، ونقل البيانات من البرامج التانية، والجرد، واقتراح الطلبية، وتنبيهات سحب هيئة الدواء، والكاشير، والبنية التحتية للسلامة الدوائية.

**القواعد كلها في [`docs/OWNER_RULES.md`](docs/OWNER_RULES.md) بس**، وقواعد الكود في [`AGENTS.md`](AGENTS.md)، والحالة في [`docs/STATUS.md`](docs/STATUS.md). الرؤية والمبادئ في [`docs/PROJECT.md`](docs/PROJECT.md).

## التشغيل على جهاز الصيدلية (Windows أو Linux)
على Windows: شغّل `scripts\windows\install.bat` مرة واحدة، وبعد كده `start.bat`.
محتاج Python 3.11 أو أحدث، والمكتبات دي: `starlette` و`uvicorn` و`python-multipart` و`openpyxl` و`pdfplumber` و`pillow` و`requests`.

```bash
pip install -r requirements.txt
python manage.py init --pharmacy "اسم الصيدلية" --username owner      # هيطلب منك كلمة المرور
python manage.py serve                                               # افتح http://127.0.0.1:8000
python manage.py backup                                              # نسخة احتياطية zip متأكد منها (ينفع تتعمل والبرنامج شغال)
python manage.py verify [backup.zip]                                 # فحص سلامة قاعدة البيانات أو نسخة احتياطية
python manage.py restore backup.zip --yes                           # استرجاع (بعد ما تقفل البرنامج)
python manage.py selftest                                            # فحص الجهاز (للـ Windows)
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
| `docs/OWNER_RULES.md` | كل القواعد (المصدر الوحيد) |
| `AGENTS.md` | قواعد الكود التقنية |
| `docs/STATUS.md` | الحالة ونسبة جاهزية النسخة الأولى |
| `docs/HANDOFF.md` | العمل الجاري والخطوة الجاية |
| `docs/PROJECT.md` | الرؤية والمبادئ والمعمارية |
| `docs/ARCHITECTURE.md` | المعمارية، قاعدة البيانات، الأجهزة الطرفية، التكاملات |
| `docs/DECISIONS.md` | القرارات التقنية وأسبابها (ADR) |
| `docs/PROGRESS.md` | سجل تاريخي (حتى 2026-10-25) |
| `docs/SECURITY.md` | الأمان، وإيه اللي اتنفذ، وإيه الناقص |
| `docs/API.md` | واجهات API (لتطبيق الموبايل والتكاملات) |
| `docs/CLINICAL_DATA_SOURCES.md` | ورقة قرار مصادر البيانات الطبية + مقارنة |
| `docs/DATABASE_EVALUATION.md` | تقييم SQLite والعمل من غير نت |
| `docs/COSTS.md` | التكاليف المتوقعة والفعلية |
| `docs/WINDOWS_ACCEPTANCE.md` | اختبار القبول على Windows والطابعة |
| `docs/GITHUB.md` | ربط GitHub وحماية البيانات |
| `docs/FUTURE_REQUIREMENTS.md` | الإضافات المستقبلية |
| `docs/specs/` | المواصفات الأصلية زي ما اتبعتت |
