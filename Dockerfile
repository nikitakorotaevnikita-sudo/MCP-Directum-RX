FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Зависимости отдельным слоем — кешируются при изменениях src/
COPY pyproject.toml ./
RUN pip install --no-cache-dir -e ".[dev]"

COPY src ./src
COPY tests ./tests

# Справочники доменов DRX для MCP-ресурсов drx://domains/* (только SKILL.md читаются сервером)
COPY .claude/skills ./.claude/skills

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
