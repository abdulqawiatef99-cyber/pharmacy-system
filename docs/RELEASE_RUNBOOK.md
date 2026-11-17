# ليلة الإصدار الأول — #20 (`v0.4.0`)

آخر تحديث: 2026-11-11 · Claude. المرجع: [#20](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/20)، وقرار [#104](https://github.com/abdulqawiatef99-cyber/pharmacy-system/issues/104) (الخيار 1)، و`LAUNCH_PLAN.md` §2 خطوة 4.

الخطوات المكتوبة بالعربي إنت اللي بتعملها أو بتوافق عليها. الأوامر بالإنجليزي اللي في المربعات أنا (Claude) اللي بشغّلها.
**كل خطوة ليها علامة ✓ أو ✗. أي ✗ = وقّف، ومفيش خطوة بعدها لحد ما نقرر.**

## 0. قبل الليلة: لازم كل ده يكون خلص

| # | الشرط | الحالة 2026-11-11 | الدليل |
|---|---|---|---|
| P1 | #18 (C2) إعادة التجربة على جهاز الصيدلية نجحت، **ومعاها تجربة الرجوع** (القسم 9 تحت) | مستنية الزيارة | #18 |
| P2 | #19 (D1) يوم التشغيل التجريبي خلص وإنت وقّعت | مستنية #18 | #19 |
| P3 | ملف التثبيت يتبني: مساحة الـ artifacts على GitHub مليانة، فبناء `main` هيفشل قبل النشر | **مفتوحة** | #122، PR #123 + موافقتك على مسح الملفات القديمة |
| P4 | رقم الإصدار: `app/version.py` لسه `0.4.0rc1`، لازم يبقى `0.4.0` **قبل** بناء مثبّت #18، عشان اللي اتجرب هو نفسه اللي ينزل | **ناقص** (PR صغير لـ `develop`) | — |
| P5 | اسم البرنامج بدل «منصة الصيدلية»: قرارك، والتغيير يدخل **قبل** مثبّت #18 عشان الاختصارات تتجرب | **مستني قرارك** | تعليق على #20 |
| P6 | بيانات البداية: الجهاز عليه بيانات من 10/24 (نقل E-Stock) وهيتعمل عليه يوم #19 ببيانات وهمية. لازم قرار: إيه اللي يبدأ بيه البرنامج يوم الإطلاق (استيراد جديد من E-Stock في قاعدة نضيفة، ولا القاعدة الحالية) ومفيش بيانات وهمية تفضل فيها | **ناقص قرار** | #19، `ESTOCK_IMPORT.md` |
| P7 | الـ SHA المقبول: commit على `develop` اتبنى منه مثبّت #18، وCI عليه أخضر linux وwindows | يتحدد بعد #18 | #18 |
| P8 | موافقتك المكتوبة على #20 (القسم 1) | مستنية | #20 |
| — | A2 = #12 (حماية الفروع): **مش شرط.** إنت قررت 2026-10-30 تتأجل لبعد الإطلاق، و#20 مش مستنياها | اتقفل كشرط | #12 |

**الوقت:** بعد قفل الصيدلية، حوالي ساعة ونص. E-Stock ما بيتلمسش في أي خطوة.

## 1. موافقتك المكتوبة (تتنسخ في #20 زي ما هي)

اكتب على #20 تعليق بالنص ده (بالأرقام الحقيقية):

> موافق على الإصدار v0.4.0 من `develop` @ `<SHA المقبول>` إلى `main`، بطريقة «release PR» (القسم 2-أ)، وتثبيته على جهاز الصيدلية الليلة بعد النسخة الاحتياطية على الفلاشة. E-Stock يفضل شغال أسبوع.

ولو اخترت طريقة إعادة ضبط `main` (2-ب) بدل 2-أ، دي **موافقة تانية منفصلة** (قرارك على #104)، لازم تتكتب لوحدها.

## 2. نقل الـ SHA المقبول إلى `main`

`feature/reference-catalog` محتفظ بالـ 12 تعديل اللي على `main` (`25328e3`)، فمفيش حاجة هتضيع في الطريقتين.

### 2-أ. المقترح: release PR (من غير إعادة كتابة `main`)
بيخلي محتوى `main` **مطابق بالظبط** للـ SHA المقبول، من غير force push. ده مهم لأن بوابة النشر
(`.github/scripts/verify_push.sh`) **بترفض أي force push** وما بتنشرش المثبّت، والحارس (`branch-guard.yml`) بيفتح Issue.
اتجربت محليًا 2026-11-11 على `develop` @ `4a93eb2` و`main` @ `25328e3`: محتوى `main` بعد الدمج = `develop` بالظبط، ومن غير force.

```bash
ACCEPTED=<accepted develop sha>
git fetch origin
git checkout -b release/v0.4.0 "$ACCEPTED"
git merge -s ours origin/main -m "release v0.4.0: main takes develop @ ${ACCEPTED:0:7} (#20, #104 option 1)"
git diff --quiet "$ACCEPTED" HEAD && echo "tree == accepted"        # must print
git merge-base --is-ancestor origin/main HEAD && echo "no force needed" # must print
git push origin release/v0.4.0
# PR release/v0.4.0 -> main. CI linux + windows run on it (a PR into main runs Windows: part of the approved release).
```
- **إنت بس** اللي تدوس Merge، وبـ **"Create a merge commit"** (مش squash ولا rebase).
- بعد الدمج: `git fetch origin main` الأول (الـ `origin/main` المحلي لسه قديم)، وبعدين `git diff --quiet "$ACCEPTED" origin/main` لازم ينجح، و`git rev-parse origin/main^2` = آخر commit في `release/v0.4.0`.
- **تحذير لبعد الإطلاق:** ماحدش يدمج `main` في `develop`. التعديلات الـ 12 بقت في تاريخ `main` من غير محتواها، فدمج عكسي يخلي git يفتكرها اتدمجت. الكتالوج يرجع من `feature/reference-catalog` لـ `develop` مباشرة (أو بعد إعادة ترقيم الـ migrations)، والإصدار الجاي `develop → main` عادي.

### 2-ب. البديل: إعادة ضبط `main` (محتاج موافقة منفصلة)
```bash
git push --force origin "$ACCEPTED":refs/heads/main
```
النتيجة المتوقعة: الحارس يفتح Issue ويحفظ `guard/main-25328e3c7851`، و**بناء المثبّت يرفض النشر** (force push).
فلازم بعده PR تاني صغير يندمج في `main` عشان يتنشر `installer-latest`. خطوات أكتر ومخاطرة أكتر، عشان كده 2-أ هو المقترح.

## 3. العلامة `v0.4.0`
```bash
git fetch origin main                   # origin/main must include the merged release PR
git diff --quiet "$ACCEPTED" origin/main || echo STOP
M=$(git rev-parse origin/main)          # the merge commit from 2-A
git tag -a v0.4.0 "$M" -m "v0.4.0: first release (develop @ ${ACCEPTED:0:7}, #20)"
git push origin v0.4.0                  # tags trigger no workflow
```

## 4. بوابة نشر المثبّت
الدمج في `main` بيشغّل "Windows installer" لوحده (مفيش تشغيل يدوي، ومفيش إعادة تشغيل من غير موافقتك).
| ✓ | الفحص |
|---|---|
| | job `gate` كاتب `verified: PR #<n>, CI / linux green on <head>` |
| | Windows tests + smoke (selftest, init, verify, update + rollback) نجحوا |
| | Release `installer-latest` اتعمل، والـ target = `$M` |
| | `build_info.json` جوه المثبّت = `{"sha": "$M", "ref": "main"}` (يتشاف بعد التثبيت، القسم 7) |

لو `gate` فشل أو الرفع فشل (#122): **وقّف.** ماتثبتش أي مثبّت تاني بداله.

## 5. النسخة الاحتياطية (قبل أي تثبيت)
على جهاز الصيدلية `DESKTOP-5JVH3QD`، والبرنامج شغال عادي. وصّل الفلاشة (هنفترض `E:`):
```bat
hostname
rem must print DESKTOP-5JVH3QD, otherwise stop
set PHARM_DATA_DIR=%LOCALAPPDATA%\PharmacyPlatform\data
set PY="%LOCALAPPDATA%\Programs\PharmacyPlatform\python\python.exe"
set M="%LOCALAPPDATA%\Programs\PharmacyPlatform\program\manage.py"
mkdir "D:\PharmacyPlatform-backup-v0.4.0"
%PY% %M% backup --dest "D:\PharmacyPlatform-backup-v0.4.0"
%PY% %M% verify "D:\PharmacyPlatform-backup-v0.4.0\<file>.zip"
%PY% %M% preflight --json "D:\PharmacyPlatform-backup-v0.4.0\<file>.zip" > "D:\PharmacyPlatform-backup-v0.4.0\before.json"
mkdir "E:\v0.4.0"
copy "D:\PharmacyPlatform-backup-v0.4.0\*" "E:\v0.4.0\"
%PY% %M% verify "E:\v0.4.0\<file>.zip"
type "%LOCALAPPDATA%\Programs\PharmacyPlatform\program\build_info.json" > "E:\v0.4.0\build_before.txt"
```
| ✓ | الخطوة |
|---|---|
| | `verify` = OK مرتين: على `D:` وعلى الفلاشة |
| | **ملف التثبيت القديم** (المتثبت دلوقتي، run 41238470755، artifact `13056706261`) متحمّل ومتنسخ على الفلاشة جنب النسخة. ده طريق الرجوع (القسم 9) |
| | اكتب على ورقة: عدد الأصناف، رصيد صنف تعرفه، رقم آخر فاتورة بيع |

## 6. التثبيت (بالمثبّت بس)
- **ماتستخدمش** زرار «حدّث البرنامج دلوقتي» ولا `update.bat` للانتقال ده. أول انتقال بالمثبّت بس (`UPDATE.md`).
- إنت بتنزّل `PharmacySetup.exe` من صفحة Releases ← `installer-latest` في المتصفح على الجهاز.
```powershell
Get-FileHash "$env:USERPROFILE\Downloads\PharmacySetup.exe" -Algorithm SHA256
# compare with the digest of the installer-latest asset (GitHub release page / API)
```
- دبل كليك على المثبّت. لو البرنامج شغال هيقولك إنه هيقفله، دوس OK (R10). Next ← Install ← Finish.

## 7. الفحوص بعد التثبيت
```bat
curl.exe -s http://127.0.0.1:8000/api/health
rem expect: "ok": true, "version": "0.4.0", "schema": the last migration (0033 on 2026-11-11), "boot_id": not null
type "%LOCALAPPDATA%\Programs\PharmacyPlatform\program\build_info.json"
rem expect: sha = M from section 3, ref = main
%PY% %M% verify
%PY% %M% backup --dest "D:\PharmacyPlatform-backup-v0.4.0"
%PY% %M% preflight --json "D:\PharmacyPlatform-backup-v0.4.0\<new file>.zip" > "D:\PharmacyPlatform-backup-v0.4.0\after.json"
%PY% -c "import json,sys; c=[json.load(open(f,encoding='utf-8'))['counts'] for f in sys.argv[1:]]; print('COUNTS SAME' if c[0]==c[1] else ('COUNTS DIFFER', c))" "D:\PharmacyPlatform-backup-v0.4.0\before.json" "D:\PharmacyPlatform-backup-v0.4.0\after.json"
rem must print COUNTS SAME (the text output of preflight has no counts; --json has)
```
وبإيدك:
| ✓ | الفحص | المطلوب |
|---|---|---|
| | صفحة «البرنامج» | الإصدار 0.4.0، والقناة `main` |
| | البيانات | عدد الأصناف، ورصيد الصنف، وآخر فاتورة **زي الورقة** |
| | الإيصال | بيع صنف بـ 1 جنيه كاش (بيانات تجربة، وبعدين مرتجع) ← الإيصال مقروء وكامل |
| | الدرج | فتح مرة واحدة مع البيع، ومرة بزرار F8 (بسبب، ويتسجل) |
| | الملصق | 5 ملصقات ورا بعض على Xprinter 38×13، كاملة ومن غير فاضي |
| | القارئ | امسح صنف مرتين ← نفس السطر الكمية بقت 2 |
| | إعادة التشغيل | اقفل الجهاز وافتحه ← البرنامج يفتح لوحده على نفس البيانات، و`boot_id` اتغير |
| | النسخة خارج الجهاز | «✓ آخر نسخة خارج الجهاز» بوقت النهارده |

بعد ما كله ✓: أكتب أنا النتيجة على #20 (SHA، رقم تشغيل المثبّت، الـ sha256، الفحوص)، ونسيب #20 مفتوحة لحد آخر أسبوع E-Stock.

## 8. أسبوع جنب E-Stock
- E-Stock يفضل شغال وهو المرجع لمدة **أسبوع تقريبًا**. البرنامج الجديد يشتغل جنبه.
- آخر كل يوم: قارن مبيعات اليوم والخزنة بين الاتنين. أي فرق = Issue جديدة فيها الرقمين، ومفيش تعديل في البيانات بإيد.
- بعد الأسبوع **إنت** اللي تقرر إمتى توقف E-Stock. لحد ساعتها `main` ما يتحركش غير بإصلاح متجرب وموافقتك.

## 9. الرجوع (لو حاجة باظت)
**المستوى 1 — البرنامج بس (البيانات سليمة):** ثبّت ملف التثبيت القديم اللي على الفلاشة فوق الجديد. المشغّل بياخد أي مثبّت جديد الـ SHA بتاعه مختلف (`release_bootstrap.adopt_installer`)، فالقديم يشتغل على نفس البيانات.
ده آمن **بس** لو مفيش migration أو تغيير مكتبات بين النسختين:
```bash
git diff --stat 7190880 "$ACCEPTED" -- app/migrations requirements.txt   # empty on 2026-11-11 (develop @ 4a93eb2)
```
لو مش فاضي، النسخة القديمة ممكن ما تفهمش القاعدة: روح للمستوى 2.

**المستوى 2 — البرنامج القديم + البيانات:** الاسترجاع بيرجّع البيانات بس، مش البرنامج. لو البرنامج الجديد فضل متثبت، أول ما يفتح هيطبّق الـ migrations بتاعته على البيانات المسترجعة تاني. فالترتيب:
1. ثبّت **ملف التثبيت القديم** من الفلاشة، وفي آخر شاشة **شيل علامة «افتح البرنامج دلوقتي»** قبل Finish.
2. أنا بقفل أي نسخة شغالة (السيرفر بيفضل شغال في الخلفية حتى لو المتصفح اتقفل)، وبعدين أسترجع **بالبرنامج القديم**، وبعدين أتأكد:
```bat
powershell -NoProfile -Command "Get-Process python,pythonw -ErrorAction SilentlyContinue | Where-Object { $_.Path -like \"$env:LOCALAPPDATA\Programs\PharmacyPlatform\*\" } | Stop-Process -Force"
curl.exe -s http://127.0.0.1:8000/api/health
rem must fail to connect before the restore
type "%LOCALAPPDATA%\Programs\PharmacyPlatform\program\build_info.json"
rem must show the OLD sha (E:\v0.4.0\build_before.txt), otherwise stop: the new program is still installed
%PY% %M% restore "E:\v0.4.0\<file>.zip" --yes
rem the data before the restore is kept in a pre-restore backup folder (printed)
%PY% %M% verify
```
3. افتح البرنامج من الأيقونة، و`/api/health` لازم يرجّع الإصدار القديم، والأرقام زي الورقة اللي في القسم 5.
أي بيع اتعمل بعد النسخة لازم يتسجل تاني بإيد (من E-Stock أو الورق)؛ عشان كده E-Stock شغال الأسبوع ده.

**المستوى 3 — `main` نفسه:** `main` ما يرجعش لورا. الإصلاح ينزل كإصدار جديد `v0.4.1` بنفس الطريقة (2-أ). التعديلات الـ 12 القديمة محفوظة في `feature/reference-catalog`.

### اللي لازم يتجرب في #18 (C2) عشان نقول الرجوع «متجرب»
| # | التجربة | المطلوب |
|---|---|---|
| RB1 | على اللابتوب أو جهاز الصيدلية: ثبّت مثبّت #18 الجديد فوق run 41238470755، وبعدين **ثبّت القديم تاني فوقه** | يفتح، و`/api/health` يرجّع الإصدار القديم، والبيانات زي ما هي |
| RB2 | `restore` لنسخة الفلاشة على جهاز تاني (R7 في `WINDOWS_ACCEPTANCE.md`) | نفس `counts` في `preflight --json` (نفس أمر المقارنة في القسم 7) |
| RB3 | المثبّت والبرنامج شغال (R10) | يقفله ويكمّل من غير Task Manager |
| RB4 | `git diff --stat` للـ migrations والمكتبات بين المثبّت المتثبت ومثبّت #18 | فاضي (لو مش فاضي، المستوى 1 مش متاح ولازم RB2 ينجح) |

R8 (رجوع التحديث من جوه البرنامج) مش طريق الرجوع هنا، لأن الانتقال بالمثبّت.

## 10. ليلة البداية النضيفة: قاعدة فاضية + استيراد جديد من E-Stock (#113)

بعد قفل الصيدلية. خطوتين، كل واحدة دبل كليك. كل خطوة بتكتب ✓ أو ✗ في ملف، وأول ✗ بتوقف كل حاجة.
الملفات بتتكتب هنا:
```
%USERPROFILE%\Documents\launch-night\
```

### أ. قبلها: التصدير من E-Stock
1. اتأكد إن آخر فاتورة بيع ومشتريات النهارده متسجلة في E-Stock.
2. اعمل فولدر جديد اسمه `estock` على `C:` (يعني `C:\estock`)، وما يكونش فيه غير الملفات دي.
3. صدّر من E-Stock بصيغة «Excel - Data Only» التقريرين دول، وسيب اسم الملف زي ما E-Stock بيكتبه:
   - «قائمة الأصناف» (دليل الأصناف) — **لازم**
   - «كميات الأصناف طبقًا لتواريخ الصلاحية» — مخزن الصيدلية — **لازم**
4. ملف عدد الوحدات اللي اتراجع في التجربة (اسمه فيه «الوحدات») — **لو موجود** حطه جنبهم. لو مش موجود، الأصناف اللي عدد وحداتها مش معروف بتتساب وتتصلح في الجرد (قرارك 24/10).
5. ماتحطش في الفولدر تقرير تاني فيه نفس الكلمات (زي «أصناف تغيرت معاملات وحداتها»): الخطوة ب هترفض لو لقت ملفين من نفس النوع.

### مكان الملفين
بعد زرار «تحديث»، الملفين بيبقوا في فولدر نسخة البرنامج اللي التحديث اختارها، مش في فولدر البرنامج المتثبت:
```
%LOCALAPPDATA%\PharmacyPlatform\data\code-releases\releases\<أحدث فولدر>\scripts\windows\
```
(لو الجهاز اتثبت عليه مثبّت أحدث من التحديث، يبقوا في `%LOCALAPPDATA%\Programs\PharmacyPlatform\program\scripts\windows\`.)

### ب. الدبل كليك الأول: `launch-fresh-start.bat`
- بيسألك: اكتب `YES`.
- بيقفل البرنامج بس (E-Stock وأي برنامج تاني ما بيتلمسوش)، وبياخد نسخة احتياطية ويتأكد منها، ولو فيه `D:` بينسخها في `D:\PharmacyPlatform-backups` ويتأكد منها تاني.
- بيغيّر **اسم** فولدر البيانات الحالي لـ `data-trial-<التاريخ-الوقت>` (مفيش أي مسح)، وبيفتح البرنامج على قاعدة فاضية.
- المطلوب: المتصفح يفتح على شاشة أول تشغيل. املاها (اسم الصيدلية، اسم المالك، كلمة المرور) وافتكر اسم المالك.
- النتيجة: `fresh-start.txt`، وآخر سطر لازم يكون `PASS - ALL DONE`.
- لو ✗ بعد تغيير الاسم: السكربت بيرجّع الفولدر القديم لاسمه لوحده، والبرنامج بيفتح على بياناته القديمة.

### ج. الدبل كليك التاني: `estock-import.bat`
- بيدور على الملفات في `C:\estock`، ويسألك اسم المالك، وكلمة المرور مرتين (الكتابة مش بتظهر).
- الأول **تجربة على نسخة** من القاعدة، ونتيجتها كاملة في `import-dry-run.txt` وعلى الشاشة. بص على: عدد الأصناف، وعدد التشغيلات، و«المطابقة: OK».
- لو تمام اكتب `YES`: بياخد نسخة احتياطية متأكد منها، وينقل، ويعمل فحص المخزون، ويفتح البرنامج.
- النتيجة: `estock-import.txt` (آخر سطر `PASS - ALL DONE`) و`import-apply.txt`.
- بعدها: قارن عدد الأصناف ورصيد 3 أصناف تعرفهم مع E-Stock. E-Stock يفضل شغال أسبوع (القسم 8).

### الرجوع للبيانات القديمة
البيانات القديمة كاملة في `%LOCALAPPDATA%\PharmacyPlatform\data-trial-<التاريخ-الوقت>`، والنسخة الاحتياطية في `%LOCALAPPDATA%\PharmacyPlatform\backups\fresh-start-<التاريخ-الوقت>`. الرجوع ليها قرارك، وأنا اللي بعمله.
