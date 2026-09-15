- **Spec: what the inbox socket leaves behind, measured.**
  `docs/superpowers/specs/2026-09-15-socket-reply-status.md` counts 47 socket
  messages delivered on this machine. Every one can be recovered from the
  receiver's transcript (`origin.kind == "peer"`, or a `queued_command`
  attachment when the receiver was busy), and all 39 `SendMessage` sends pair
  with their receipt. Replies look one-way only for raw socket writes, which
  carry no return address. Recommends no send log, and names one defect: the
  unblock detector stores Claude Code's peer framing as the answer for 8 of
  27 markers. (channels)
