# Review: PR #8 «Add deterministic address verification pipeline v2»

Ветка: `feat/address-verification-v2-clean` · база: `main` @ `9b0c7a3`
Проверял: coding-агент на целевом Raspberry Pi, `pytest` + живой прогон DaData.

**Вердикт: request changes.** Идея кросс-чека здравая и диагностика полезная, но
в текущем виде мержить нельзя — красный `pytest` + гейт `VERIFIED` недостижим для
нормальных адресов.

---

## 🔴 Блокер 1. Падает собственный тест PR

```
FAILED tests/test_address_verification_v2.py::test_clean_and_suggestion_house_id_disagreement_is_rejected
1 failed, 43 passed
```

Тест-фикстура: `clean_address()` (house `"15"`) vs
`suggestion(house="151", house_fias_id="house-151")`. Номера домов различаются
**и текстом**, поэтому в `evaluate_verification` порядок проверок такой:

1. `_score_suggestion_components(...)` → `score_dadata_candidate` видит
   `house_mismatch` → `suggest_mismatch = True`
2. `if suggest_mismatch: return VerificationDecision(REJECTED, ..., + ["suggestion_component_mismatch"])`
   — **выход здесь**
3. до `_same_address(...)` (единственное, что выдаёт `"house_fias_id_mismatch"` /
   `"clean_suggest_disagree"`) исполнение не доходит

`decision.reasons` = `[... 'house_mismatch', 'suggestion_component_mismatch']`,
а тест ждёт `"house_fias_id_mismatch"` и `"clean_suggest_disagree"`. Статус
`REJECTED` правильный, но reason-строки не те.

**Как чинить (одно из):**

- **A.** Поправить фикстуру: дом должен совпадать текстом
  (`suggestion(house="15", house_fias_id="house-151")`), тогда
  `score_dadata_candidate` не поднимет `house_mismatch`, исполнение дойдёт до
  `_same_address`, и тот вернёт `house_fias_id_mismatch`. Это и есть сценарий,
  который тест декларирует («ФИАС домов разошёлся»).
- **B.** Переставить проверки в `evaluate_verification`: сравнение
  `house_fias_id` (через `_same_address`) выполнять **до** ветки
  `suggest_mismatch`. ФИАС-идентификатор — более сильный сигнал, чем текстовый
  компонент-скоринг, и его стоит проверять первым.

---

## 🔴 Блокер 2. VERIFIED практически недостижим

Живой прогон `data/input/route.xlsx` (10 адресов, реальный DaData, свежий кэш):

| №   | статус       | conf | qc / qc_complete / qc_house / qc_geo | crosscheck |
|-----|--------------|------|-------------------------------------|------------|
| 345 | review       | 0.79 | 0 / 0  / 2  / **1** | 0 м |
| 337 | review       | 0.79 | 1 / 9  / 10 / 0     | —   |
| 275 | review       | 0.79 | 0 / **10** / **10** / 0 | 0 м |
| 270 | review       | 0.79 | 0 / **10** / **10** / 0 | 0 м |
| 237 | review       | 0.79 | 0 / **10** / **10** / 0 | 0 м |
| 210 | review       | 0.79 | 0 / 0  / 2  / **1** | 0 м |
| 186 | **verified** | 0.99 | 0 / 0  / 2  / 0     | 0 м |
| 159 | review       | 0.79 | 0 / **10** / **10** / 0 | 0 м |
| 193 | review       | 0.79 | 0 / **10** / **10** / 0 | 0 м |
| 377 | review       | 0.79 | 0 / **10** / **10** / 0 | 0 м |

Итог: **1 verified, 9 review, 0 rejected.** Маршрут строится
(`feasible=True`, те же 73 179 м), но:

- `strict_clean` требует одновременно `qc_complete in {0,5}` **и**
  `qc_house == 2` **и** `qc_geo == 0`.
- На реальных питерских адресах с квартирой DaData отдаёт `qc_complete = 10`
  (разобрано до квартиры — лучший исход) и `qc_house = 10` для 6 из 10. Ни то,
  ни другое гейт не принимает → REVIEW.
- Даже самые чистые (№345, №210: `qc=0, qc_complete=0, qc_house=2`) не проходят
  из-за `qc_geo == 1` («ближайший дом»), а не `0`. Единственный VERIFIED (№186)
  имеет `qc_geo == 0` — то есть VERIFIED сейчас = «DaData дала координату точно
  на дом», что редкость.
- Через PR #4 любой `review` вешает ⚠ «требует проверки». Получается
  предупреждение на 9 точках из 10 — сигнал обесценивается.

Против уже влитого PR #7 (resolver + hardening) это регресс полезности: там
было `confidence` 0.99 и 3 «resolved», а `rejected` ловил сильные расхождения.
PR #8 за 10 адресов не отклонил ни одного и добавил только шум.

**Что просьба сделать:**

1. Ослабить `strict_clean` так, чтобы обычный точный питерский адрес проходил
   как VERIFIED без ручной проверки. Разумный вариант:
   - `qc == 0`
   - `qc_house == 2`
   - `qc_geo in {0, 1}` (ближайший дом для курьерской навигации приемлемо)
   - `clean_score >= 0.80 and suggest_score >= 0.80`
   - кросс-чек `_same_address` == True и `distance <= 200`
   - убрать жёсткое `qc_complete in {0,5}` (или расширить до `{0, 5, 10}` —
     10 = разобрано до квартиры).
2. Проверить, что именно возвращает DaData Clean в `qc_house` — наблюдается `10`
   на 6 из 10 адресов, а гейт и часть логики завязаны на `== 2`. Ваш же тест
   `test_house_not_in_fias_is_review_not_silent_verified` использует
   `qc_house=10`, то есть значение известно, но VERIFIED с ним структурно
   невозможен. Нужно понять семантику `qc_house=10` и учесть её (сырой ответ
   Clean на 2–3 адреса — в описание PR).
3. Приложить свежий live-прогон на репрезентативной выборке с разбивкой
   verified/review/rejected после перекалибровки.

---

## 🟡 Мелочи

- `src/courier_router/cli.py` — PR убирает завершающий перевод строки
  (`\ No newline at end of file`). Вернуть.
- `VerifiedDaDataGeocoder.geocode` **всегда** делает и `_clean`, и `_suggest` —
  это два платных вызова Clean на каждый новый адрес против одного в PR #7
  (Clean только в fallback). В описании отмечено, но для боевого free-tier это
  удвоение дневного лимита Clean. Рассмотреть short-circuit: если Clean уже
  даёт verified-уровень (`qc==0, qc_house==2, qc_geo<=1`) — не дёргать
  Suggestions, и наоборот.
- Две ветки в origin: `feat/address-verification-v2` и
  `feat/address-verification-v2-clean`. PR на `-clean`, вторую удалить.
- `evaluate_verification` возвращает `confidence` жёстко (0.99 / 0.10–0.79),
  не из скоров. `review` всегда сжат в `[0.45, 0.79]` → всегда ниже порога 0.80
  из PR #4 → всегда флажок. Если намеренно — ок, но см. п.1 калибровки.

---

## 🟢 Что хорошо

- Решение VERIFIED детерминированное, LLM в нём не участвует — соответствует
  инварианту `ARCHITECTURE.md`.
- Кросс-чек Clean ↔ Suggestions ↔ `house_fias_id` ↔ расстояние координат —
  правильная идея, ловит реальный класс ошибок.
- Диагностика в `geocoding-report.json` (`clean_quality`,
  `crosscheck_distance_m`, оба `*_house_fias_id`, reasons) — реально помогает
  разбирать спорные адреса.
- REJECTED не пишется в кэш даже при `--allow-low-confidence` — правильно,
  override одного запуска не отравит будущие маршруты.
- Маршрутизацию не ломает: live-прогон `feasible`, дистанция та же.
