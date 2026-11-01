import { Kafka } from "kafkajs";
import { NotificationService } from "./notification.service";

const kafka = new Kafka({ clientId: "notification-service", brokers: ["kafka:9092"] });

export async function startConsumers(): Promise<void> {
  const consumer = kafka.consumer({ groupId: "notification-group" });
  await consumer.connect();
  await consumer.subscribe({ topic: "payment.completed", fromBeginning: false });
  await consumer.subscribe({ topic: "booking.cancelled", fromBeginning: false });

  const service = new NotificationService();
  await consumer.run({
    eachMessage: async ({ topic, message }) => {
      const payload = message.value?.toString() ?? "{}";
      if (topic === "payment.completed") {
        await service.send("guest@example.com", "PAYMENT_RECEIPT", { payload });
      }
      if (topic === "booking.cancelled") {
        await service.send("guest@example.com", "CANCELLATION", { payload });
      }
    },
  });
}

export async function publishAudit(topic: string, payload: string): Promise<void> {
  const producer = kafka.producer();
  await producer.connect();
  await producer.send({ topic: "notification.sent", messages: [{ value: payload }] });
  await producer.disconnect();
}
