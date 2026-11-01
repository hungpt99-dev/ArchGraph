import express from "express";
import { startConsumers } from "./kafka";
import { NotificationService } from "./notification.service";

const app = express();
app.use(express.json());

const notificationService = new NotificationService();

app.get("/api/notifications/:guestEmail", async (req, res) => {
  const items = await notificationService.listForGuest(req.params.guestEmail);
  res.json(items);
});

app.post("/api/notifications/send", async (req, res) => {
  const result = await notificationService.send(req.body.guestEmail, req.body.template, req.body.payload);
  res.status(202).json(result);
});

app.get("/health", (_req, res) => res.json({ status: "ok" }));

startConsumers().catch((error) => {
  console.error("consumer startup failed", error);
  process.exit(1);
});

app.listen(8087, () => console.log("notification-service listening on 8087"));
