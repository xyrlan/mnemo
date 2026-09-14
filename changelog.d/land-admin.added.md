- **`mnemo land --merge --admin`** passes `--admin` through to `gh pr merge`,
  for a repo whose branch protection the maintainer owns and is choosing to
  bypass — on this one, master requires a code-owner approval nobody else
  can give, so every landing is one. Never implied: without the flag a
  protection that refuses the merge still stops the landing with `gh`'s
  reason, exactly as before. Found on the first real landing (#245's three
  pieces), where the rehearsal passed and the merge step could not.
