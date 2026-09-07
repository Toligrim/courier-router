# Validation status before handoff

Дата подготовки: 2026-09-07.

Проверено в среде сборки:
- `python3 -m compileall` для `src/`, `scripts/`, `tests/` — OK.
- AST parsing всех Python-модулей — OK.
- Независимые от OR-Tools тесты parsing/render — 4 passed.
- OR-Tools 9.15.6755 отдельно проверен по PyPI: существует CPython 3.13
  manylinux aarch64 wheel.
- OSRM 26.8.0-debian проверен по GitHub Container Registry: опубликован linux/arm64.

Что не удалось выполнить внутри среды сборки:
- Полный `pip install -e '.[dev]'`: runtime, в котором собирался ZIP, не имеет
  исходящего доступа к PyPI.
- Поэтому `tests/test_optimizer.py` здесь не исполнялся: OR-Tools не был
  предустановлен.
- Live вызовы DaData/ORS/OpenAI/Anthropic также не выполнялись.

Это не скрывается: агент на целевой Raspberry обязан выполнить полный install,
`pytest -q`, `courier-route doctor` и smoke-run согласно AGENT_DEPLOYMENT_PROMPT.md.

При любом несовпадении API в optional LLM-адаптере сначала сохранить базовый
DaData + ORS/OSRM + OR-Tools pipeline рабочим; LLM выключен по умолчанию.
