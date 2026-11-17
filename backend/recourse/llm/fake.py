"""`FakeLLM`: a scripted `LLMClient` for tests and offline runs. No network, no API key.

Script answers per purpose, in order:

    fake = FakeLLM({
        "intake.extract": [ExtractionOutput(...), LLMRefusalError("declined")],
        "draft.complaint": ["Dear Sir/Madam, ..."],
    })

Each entry is one of:
* a Pydantic model or a dict (for `structured`), or a string (for `text`);
* an `LLMError` (or any exception), which is raised, to test failure handling;
* a callable `(LLMRequest) -> answer`, to compute the answer from the request.

Every call renders the prompt exactly as the real client does (`render_prompt`) and records
it in `fake.calls`, so tests can check wrapping and redaction. A call with no scripted answer
left raises `AssertionError`: tests should say exactly what they expect.
"""

from __future__ import annotations

from collections import defaultdict, deque
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

from recourse.llm.base import LLMRequest, LLMResult, LLMUsage, ModelRole
from recourse.llm.prompting import RenderedPrompt, render_prompt

FAKE_MODELS = {ModelRole.MAIN: "fake-main", ModelRole.EXTRACT: "fake-extract"}

Answer = BaseModel | dict[str, Any] | str | BaseException | Callable[[LLMRequest], Any]


@dataclass(frozen=True)
class FakeCall:
    request: LLMRequest
    prompt: RenderedPrompt
    schema: type[BaseModel] | None


class FakeLLM:
    def __init__(self, script: Mapping[str, Sequence[Answer]] | None = None) -> None:
        self._script: dict[str, deque[Answer]] = defaultdict(deque)
        for purpose, answers in (script or {}).items():
            self._script[purpose].extend(answers)
        self.calls: list[FakeCall] = []

    def add(self, purpose: str, *answers: Answer) -> None:
        self._script[purpose].extend(answers)

    def calls_for(self, purpose: str) -> list[FakeCall]:
        return [c for c in self.calls if c.request.purpose == purpose]

    @property
    def unused(self) -> dict[str, int]:
        """Scripted answers that were never consumed (assert this is empty at test end)."""
        return {p: len(q) for p, q in self._script.items() if q}

    async def structured[M: BaseModel](self, request: LLMRequest, schema: type[M]) -> LLMResult[M]:
        answer = self._next(request, schema)
        if isinstance(answer, schema):
            parsed = answer
        elif isinstance(answer, dict):
            parsed = schema.model_validate(answer)
        else:
            raise TypeError(
                f"FakeLLM: {request.purpose} expected {schema.__name__} or dict, "
                f"got {type(answer).__name__}"
            )
        return LLMResult(output=parsed, served_model=FAKE_MODELS[request.role], usage=LLMUsage())

    async def text(self, request: LLMRequest) -> LLMResult[str]:
        answer = self._next(request, None)
        if not isinstance(answer, str):
            raise TypeError(f"FakeLLM: {request.purpose} expected str, got {type(answer).__name__}")
        return LLMResult(output=answer, served_model=FAKE_MODELS[request.role], usage=LLMUsage())

    def _next(self, request: LLMRequest, schema: type[BaseModel] | None) -> Any:
        self.calls.append(FakeCall(request=request, prompt=render_prompt(request), schema=schema))
        queue = self._script.get(request.purpose)
        if not queue:
            raise AssertionError(f"FakeLLM: no scripted answer left for {request.purpose!r}")
        answer = queue.popleft()
        if isinstance(answer, BaseException):
            raise answer
        if callable(answer) and not isinstance(answer, BaseModel):
            answer = answer(request)
        return answer
