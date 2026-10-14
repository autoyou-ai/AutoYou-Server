# Browser routes and everyday defaults

Live View lists every route reported by the server in a horizontally scrollable
tile strip. Each tile shows its address, route type, access rule, and Open action.
Open connectivity selects Routes & delivery and brings Browser routes above the
notification queue. The route settings use the full available width and stack
their actions on smaller screens. Reset access restores an agent's default OTP
rule; Set default selects the website connected browser clients open.

Fresh installations enable Admin, Ads Watching, Audio, Backup, Client Browser
Control, Donation, Earnings, Game, Internet, Memory, Notes, Notify, Page, Persona,
My Prompt, Remote Desktop, Tasks, and TV. The everyday agent websites open without
an additional OTP prompt by default. Explicit per-agent OTP requirements still
win. The Admin website remains opt-in for paired browsers and retains its own
authentication. Installing Remote Desktop does not enable capture or input
permissions. TV contains only media the user chooses to publish.

New paired-browser preferences default to Editor. Saved Viewer, Editor, and Admin
choices are preserved. Missing or malformed forwarded identity claims still
receive Viewer permissions.

Phone publishing supports Notes, Page, and Diary attachments up to 64 MiB on iOS
and Android. Notes and Diary accept the same limit on the server. Photos are
published at their original quality. Empty files and oversized files receive
different errors, and validation happens before creating remote content. The
Media tab retains its resumable 20 GiB TV upload path for larger photos and
videos. Existing mobile apps must be rebuilt or updated to use the higher limit.
