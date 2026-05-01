## Workflow
- Update CHANGELOG.md with every file create/edit/delete.
- Commit after each minor phase completes.
- Add and run tests before moving forward.

## Code Style
- Keep it simple. Avoid redundancy and tech debt.
- Use custom exceptions from `src/exceptions.py` for domain errors.
- Use `logging.getLogger(__name__)` for all module loggers.
- Follow existing Pydantic model patterns in `src/models/`.

## Testing
- Run tests via: `docker exec options_agent-dashboard-1 python3 -m pytest tests/` or activate the local venv first.
- Never use bare `python3` outside a venv or container.

## Project Layout
- `src/` — core modules (agent, analyzers, risk, strategies)
- `src/models/` — Pydantic data models
- `src/exceptions.py` — custom exception hierarchy
- `config/settings.py` — Pydantic Settings (env/.env)
- `scripts/` — CLI entry points
- `dashboard/` — Streamlit UI
- `audit_logs/` — JSON-lines audit trail (git-ignored)