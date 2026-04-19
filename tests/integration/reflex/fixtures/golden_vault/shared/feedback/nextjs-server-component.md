---
name: nextjs-server-component
description: Default nextjs route segments to server components and opt into client only when needed
tags:
  - nextjs
  - server-components
aliases:
  - rsc
  - server
  - component
sources:
  - bots/projA/memory/nextjs-rsc.md
  - bots/projB/memory/nextjs-server.md
stability: stable
---
In the nextjs app router, default every route segment to a server component. A nextjs server component runs only on the server, ships zero javascript to the browser, and can talk directly to the database. Only mark a subtree as a client component when it needs interactive state or browser-only apis. Sprinkling use client at the top of every file undoes the entire nextjs app router performance story because it pulls the subtree into the client bundle. Keep a clean nextjs layering: server components compose data fetching, client components own interactivity.
