# yay-llm-review

`yay-llm-review` is an opt-in security-review companion for yay 13. It
integrates through an `AURPreInstall` Lua hook that sends the checked-out AUR
recipe to an OpenAI-compatible `llama.cpp` server before source downloads and
builds begin. It can also manually review an existing AUR checkout.

The project is maintained independently of Arch Linux packaging. An optional
Arch package is available in the [AUR](https://aur.archlinux.org/packages/yay-llm-review).

It reviews:

- the full tracked AUR repository (`PKGBUILD`, `.install`, patches and scripts);
- the Git diff from `AUR_SEEN` when available, otherwise from the previous commit;
- deterministic warning signals such as pipe-to-shell downloads, obfuscated
  execution, setuid creation, credential paths and disabled checksums.

Package contents are explicitly framed as untrusted data in the model prompt.
This reduces, but cannot eliminate, prompt-injection and model errors. Keep
`yay`'s normal diff review enabled.

## Install

```sh
yay -S yay-llm-review
```

The installed program does not modify any user's home directory during
installation. Initialize it as the user who runs yay:

```sh
yay-llm-review init
```

This creates:

```text
~/.config/yay/init.lua
~/.config/yay-llm-review/config.toml
```

The generated configuration contains `enabled = false`. Edit it, set the
llama.cpp endpoint and model, then enable it:

```toml
enabled = true
endpoint = "http://kicer.lan:3030/v1"
model = "local-model"
```

Check the effective setup:

```sh
yay-llm-review status
```

Manual test against an existing AUR checkout:

```sh
yay-llm-review scan ~/.cache/yay/some-package
```

## Test the configured model

Use the built-in diagnostic suite to verify that the configured llama.cpp server
is reachable, returns a valid review, and recognizes a safe recipe as well as
several suspicious `PKGBUILD` patterns:

```sh
yay-llm-review test
```

Add `--verbose` to print the model's confidence and all findings for each
scenario:

```sh
yay-llm-review test --verbose
```

The suite sends one benign recipe and three intentionally suspicious recipes
(download-and-execute, disabled checksums, and credential access). A scenario
passes only when the benign recipe is allowed with a `safe` or `low` risk level,
or when a suspicious recipe is returned as non-safe. This matches the hook,
which warns or blocks on every non-safe risk level regardless of the model's
recommended action. The command reports each scenario before it sends the
request and shows a spinner while waiting when progress indication is enabled.
It exits with `0` only if every scenario passes, `1` if the model's
classifications do not meet those expectations, and `30` if the server cannot
be contacted or returns an invalid response. It does not use the review cache.

Exit statuses are `0` for allow, `10` for warning, `20` for block and `30` for
scanner failure.

## Progress indication

On a terminal, the hook displays a spinner while waiting for `llama.cpp`.
For redirected output it prints one persistent status line instead. Disable it with:

```toml
show_progress = false
```

## Policy

The model returns one of `safe`, `low`, `medium`, `high`, `critical`, or
`uncertain`. Every non-safe result is displayed. `block_threshold` controls
which risk level aborts yay; the default is `critical`. `uncertain` always
warns. `on_error` controls whether transport and response errors warn or block.

A conservative starting point is:

```toml
block_threshold = "critical"
on_error = "warn"
```

After observing the selected model's false-positive rate, changing the block
threshold to `high` is reasonable.

## Disable or remove the loader

Set `enabled = false`, or remove only the managed loader block:

```sh
yay-llm-review deinit
```

The user configuration is deliberately retained.

## Security boundaries

This is an advisory layer, not a sandbox or proof of safety. It does not inspect
source archives before `AURPreInstall`, execute shell code safely, or guarantee
that a model notices obfuscated behavior. A malicious AUR recipe may also target
bugs in `makepkg`, compilers, archive tools, or upstream source code. Review the
normal yay diff and build untrusted packages in an appropriately isolated
environment.
