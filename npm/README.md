# @xyrlan/mnemo (npm wrapper)

One-command installer for [mnemo](https://github.com/xyrlan/mnemo).

```
npx @xyrlan/mnemo install              # global, default
npx @xyrlan/mnemo install --project    # only in <cwd>
npx @xyrlan/mnemo uninstall
```

This package is a thin Node bootstrap. The actual mnemo runtime is a Python
package installed automatically via `uv`, `pipx`, or `pip --user` (whichever
is available). Python 3.8+ is required.

See the main project README for usage details.
