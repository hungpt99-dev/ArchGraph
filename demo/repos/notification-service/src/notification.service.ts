import Redis from "ioredis";

export interface NotificationRecord {
  guestEmail: string;
  template: string;
  status: string;
}

export class NotificationService {
  private redis = new Redis({ host: "redis", port: 6379 });

  async send(guestEmail: string, template: string, payload: unknown): Promise<NotificationRecord> {
    const key = `notifications:${guestEmail}`;
    const record: NotificationRecord = { guestEmail, template, status: "QUEUED" };
    await this.redis.set(key, JSON.stringify(record));
    await this.deliver(record, payload);
    return record;
  }

  async listForGuest(guestEmail: string): Promise<NotificationRecord[]> {
    const raw = await this.redis.get(`notifications:${guestEmail}`);
    return raw ? [JSON.parse(raw) as NotificationRecord] : [];
  }

  private async deliver(record: NotificationRecord, payload: unknown): Promise<void> {
    const response = await fetch("https://api.sendgrid.com/v3/mail/send", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ to: record.guestEmail, template: record.template, payload }),
    });
    record.status = response.ok ? "SENT" : "FAILED";
  }
}
