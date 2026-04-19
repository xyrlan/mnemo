---
name: react-state-key
description: Force react component remount by changing the key prop to reset state
tags:
  - react
  - state
  - remount
aliases:
  - chave
  - remount
  - reset
sources:
  - bots/projA/memory/react-key.md
  - bots/projB/memory/react-remount.md
stability: stable
---
When a react component owns local state and you need to reset that state on a prop change, change the key prop on the component. React treats a new key as a new identity, unmounts the old instance, and remounts a fresh one with pristine state. This remount trick is cheaper than threading reset logic through useEffect. Common uses are form components that need to reset after a submission and modal contents that need a clean state each time they open. Do not rely on key for performance — it forces a remount every time, which re-runs every effect and re-initialises every piece of state in that react subtree.
