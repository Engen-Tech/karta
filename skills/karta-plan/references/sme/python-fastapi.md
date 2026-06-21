---
name: python-fastapi
description: Python + Pydantic + FastAPI do's and don'ts
match: ["fastapi", "pydantic", "python"]
see_also: ["platform-native#python-standard-library", "platform-native#database"]
---
## Do
- Define request and response bodies as Pydantic models; set `response_model` on routes.
- Use type hints on every function signature; let FastAPI derive validation from them.
- Use dependency injection (`Depends`) for shared resources (DB sessions, auth, settings).
- Use `async def` for I/O-bound path operations; keep blocking work off the event loop.
- Load configuration through `pydantic-settings` (`BaseSettings`), not bare `os.environ` reads scattered in code.
- Raise `HTTPException` (or a registered exception handler) for error responses; return typed models for success.

## Don't
- Don't return raw `dict`s from routes when a response model fits; don't leak ORM models directly as response bodies.
- Don't use mutable default arguments; don't use bare `except:`.
- Don't do blocking I/O (sync DB driver, `requests`, `time.sleep`) inside an `async def` route.
- Don't put secrets or environment-specific values in source; read them through settings.
- Don't disable Pydantic validation to make a check pass.

## Patterns
- Routers per resource (`APIRouter`), included into the app; keep `main.py` thin.
- A service/repository layer between routes and the data store; routes stay declarative.
- Pydantic v2 idioms: `model_config`, `field_validator`, `model_validator`; `ConfigDict` over class-based `Config`.

## Review checklist
- [ ] Every changed route declares request/response types (Pydantic model or explicit `response_model`).
- [ ] No bare `except:` and no mutable default arguments in changed code.
- [ ] No blocking I/O inside an `async def` route.
- [ ] New config/secrets read through a settings object, not inline `os.environ`.
- [ ] Changed public functions carry type hints on params and return.
- [ ] No third-party wrapper for what the stdlib ships (`uuid`, `pathlib`, `zoneinfo`, `datetime.fromisoformat`) — name the stdlib equivalent (see platform-native).
