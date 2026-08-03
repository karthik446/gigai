"""Prototype a generic Click command from a strict Pydantic input model."""

from __future__ import annotations

import types
from pathlib import Path
from typing import Any, Literal, Union, get_args, get_origin

import click
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from pydantic_core import PydanticUndefined


class ReviewInput(BaseModel):
    """Representative workflow input contract for the annotation spike."""

    model_config = ConfigDict(extra="forbid")

    target: str = Field(description="Goal, document, or change to review.")
    kind: Literal["code", "design", "plan", "db-schema"] = Field(
        description="Review surface."
    )
    workspace: Path = Field(
        default=Path("."),
        description="Target workspace.",
    )
    since: str | None = Field(
        default="origin/main",
        description="Diff base for code review.",
    )
    challenge: bool = Field(
        default=False,
        description="Add an independent challenger.",
    )
    references: bool = Field(
        default=True,
        description="Use fresh references when available.",
    )
    context: list[Path] = Field(
        default_factory=list,
        description="Additional context path; repeatable.",
    )
    max_findings: int = Field(
        default=20,
        ge=1,
        le=100,
        description="Maximum findings to return.",
    )


def _unwrap_optional(annotation: Any) -> tuple[Any, bool]:
    origin = get_origin(annotation)
    if origin in (Union, types.UnionType):
        args = get_args(annotation)
        non_none = tuple(arg for arg in args if arg is not type(None))
        if len(non_none) == 1 and len(non_none) != len(args):
            return non_none[0], True
    return annotation, False


def _click_type(annotation: Any) -> click.ParamType | type:
    annotation, _optional = _unwrap_optional(annotation)
    origin = get_origin(annotation)
    if origin is Literal:
        return click.Choice([str(value) for value in get_args(annotation)])
    if annotation is Path:
        return click.Path(path_type=Path)
    if annotation in (str, int, float):
        return annotation
    raise TypeError(f"unsupported CLI annotation: {annotation!r}")


def _field_default(field: Any) -> Any:
    if field.default is not PydanticUndefined:
        return field.default
    if field.default_factory is not None:
        return field.default_factory()
    return None


def build_command(
    model_type: type[BaseModel],
    *,
    name: str,
    primary_input: str | None = None,
) -> click.Command:
    """Build a Click command using only Pydantic field annotations."""

    params: list[click.Parameter] = []
    for field_name, field in model_type.model_fields.items():
        cli_name = field_name.replace("_", "-")
        description = field.description or ""
        annotation, _optional = _unwrap_optional(field.annotation)
        origin = get_origin(annotation)

        if field_name == primary_input:
            params.append(
                click.Argument(
                    [field_name],
                    required=field.is_required(),
                    type=_click_type(annotation),
                )
            )
            continue

        default = _field_default(field)
        if annotation is bool:
            params.append(
                click.Option(
                    [f"--{cli_name}/--no-{cli_name}", field_name],
                    default=default,
                    show_default=True,
                    help=description,
                )
            )
            continue

        if origin is list:
            item_type = get_args(annotation)[0]
            params.append(
                click.Option(
                    [f"--{cli_name}", field_name],
                    multiple=True,
                    required=field.is_required(),
                    type=_click_type(item_type),
                    help=description,
                )
            )
            continue

        params.append(
            click.Option(
                [f"--{cli_name}", field_name],
                required=field.is_required(),
                default=default,
                show_default=default is not None,
                type=_click_type(annotation),
                help=description,
            )
        )

    def invoke(**raw: Any) -> None:
        for field_name, field in model_type.model_fields.items():
            if get_origin(_unwrap_optional(field.annotation)[0]) is list:
                raw[field_name] = list(raw[field_name])
        try:
            value = model_type.model_validate(raw)
        except ValidationError as exc:
            raise click.ClickException(str(exc)) from None
        click.echo(value.model_dump_json(indent=2))

    return click.Command(
        name=name,
        help=model_type.__doc__,
        params=params,
        callback=invoke,
    )


review = build_command(
    ReviewInput,
    name="review",
    primary_input="target",
)


if __name__ == "__main__":
    review()
