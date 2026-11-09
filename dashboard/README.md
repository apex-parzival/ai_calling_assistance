# Front desk dashboard

Next.js app for clinic staff: sign in, call list (New / Urgent / Today, search), call detail with summary,
transcript, appointment requests and messages, and follow-up actions. Live updates over the backend WebSocket,
with an optional sound (turn it on with the button; browsers need a click before playing audio).

```bash
npm install
npm run dev        # http://localhost:3000, expects the backend on http://localhost:8000
```

Set `NEXT_PUBLIC_BACKEND_URL` (see `.env.example`) if the backend is elsewhere. The backend's
`DASHBOARD_ORIGIN` must match this app's URL for login cookies and the WebSocket to work.
