# Design: iOS Capture Companion (not started)

Not started: the design waits for the owner's yes on `requirements.md` in this folder. The first unit after that yes
is a pointing record contract (Zod in `packages/contracts`, Pydantic in `services/contracts`, a shared fixture), so
that the app and the server read the same shape. The pose estimate the web app already draws
(`PoseEstimateSchema`, spec `predicted-sky-for-every-upload`) is where a recorded pointing would feed the predicted
sky lined up over a photo, as a new pose method labelled predicted and approximate.
