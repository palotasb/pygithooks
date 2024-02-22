# pygithooks

`pygithooks` manages the [Git hooks](https://git-scm.com/docs/githooks),
such as the `pre-commit` hooks of a project without trying to also be a dependency manager.
`pygithooks` relies on the project's existing dependency manager, such as
Poetry, PDM, uv, or a simple venv managed directly via pip.

## Add it to a project

`pygithooks` is meant to be added as a development dependency to Python projects.
Depending on how you manage development dependencies, you can install it via one of these ways:

With pip:

```shell
pip install pygithooks
```

With Poetry:

```shell
poetry add --group dev pygithooks
```

With PDM:

```shell
pdm add --dev --group dev pygithooks
```

By editing `pyproject.toml`:

```toml
[project.optional-dependencies]
dev = [
    # ...
    "pygithooks",
    # ...
]
```

By editing `setup.py`:

```py
setup(
    ...,
    extras_require={
        "dev": [
            ...,
            "pygithooks",
            ...,
        ],
    },
)
```

Or edit your `setup.py`, `setup.cfg`, or `pyproject.toml` directly,
to add `pygithooks` and install it via `pip install --editable '.[dev]'`.

Either way, you should add it to your project the same way you would add
`pytest`, `twine`, `mypy`, `ruff`, `black`,
or similar dependencies that are used by project developers but not used by the package at runtime.

## Use it with a project

...

## Contribute

...
