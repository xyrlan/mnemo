- **`claude -p` gets the prompt and gives the reply in UTF-8 on Windows.**
  `llm._invoke_once` piped both through the locale's codec, cp1252 there:
  the first emoji in a transcript raised in the stdin writer and `claude`
  got an empty stdin ("no stdin data received in 3s"), so `mnemo briefing`
  never wrote one on a Windows machine; accents went over as cp1252 bytes,
  which `claude` reads as broken UTF-8; and a reply was decoded as cp1252
  (`ação` → `aÃ§Ã£o`). Setting `PYTHONUTF8=1` was the workaround and is no
  longer needed. A lone surrogate in a prompt is replaced instead of failing
  the call. 53 other text-mode subprocess calls under `src/` still decode in
  the locale's codec; none of them sends input.
