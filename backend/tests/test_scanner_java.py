"""Unit tests for the Java/Spring extractor (deterministic, no LLM)."""

from __future__ import annotations

from app.scanner.base import RepoContext
from app.scanner.java import JavaExtractor

JAVA = """
package com.acme.payments;

import org.springframework.web.bind.annotation.*;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.cloud.openfeign.FeignClient;

@RestController
@RequestMapping("/api/payments")
public class PaymentController {

    private final PaymentService paymentService;

    public PaymentController(PaymentService paymentService) {
        this.paymentService = paymentService;
    }

    @PostMapping("/authorize")
    @PreAuthorize("hasRole('SERVICE')")
    public PaymentResponse authorize(@Valid @RequestBody PaymentRequest request) {
        return paymentService.authorize(request);
    }

    @GetMapping("/{id}")
    public PaymentResponse get(@PathVariable Long id) {
        return paymentService.get(id);
    }
}

@Service
class PaymentService {

    private static final String TOPIC = "payment.completed";
    private final KafkaTemplate<String, Object> kafkaTemplate;

    PaymentService(KafkaTemplate<String, Object> kafkaTemplate) {
        this.kafkaTemplate = kafkaTemplate;
    }

    @Transactional
    public PaymentResponse authorize(PaymentRequest request) {
        kafkaTemplate.send(TOPIC, "1", payload);
        return repository.save(request);
    }

    @Scheduled(cron = "0 0/5 * * * *")
    public void retry() {
        kafkaTemplate.send("payment.retry", "1", payload);
    }
}

@FeignClient(name = "invoice-service")
interface InvoiceClient {
    @PostMapping("/api/invoices")
    Invoice create(InvoiceRequest request);
}

@Entity
@Table(name = "payments")
class Payment {
    @Id
    private Long id;
    private String status;
}

@Component
class CancellationListener {
    @KafkaListener(topics = "booking.cancelled", groupId = "payments")
    public void onMessage(String payload) { }
}
"""


def extract(path: str = "src/main/java/com/acme/payments/PaymentController.java"):
    ctx = RepoContext(repository_id="r1", repository_name="payment-service",
                      commit="abc1234", root=".", app_name="payment-service")
    return JavaExtractor().extract(path, JAVA, ctx), ctx


def test_package_and_class_detection():
    result, _ = extract()
    packages = [n for n in result.nodes if n.type == "PACKAGE"]
    classes = {n.name for n in result.nodes if n.type in ("CLASS", "INTERFACE")}
    assert packages and packages[0].name == "com.acme.payments"
    assert {"PaymentController", "PaymentService", "Payment", "InvoiceClient",
            "CancellationListener"} <= classes


def test_spring_rest_endpoints_with_auth_and_dto():
    result, ctx = extract()
    endpoints = {(n.properties["http_method"], n.properties["path"]): n
                 for n in result.nodes if n.type == "API_ENDPOINT"}
    assert ("POST", "/api/payments/authorize") in endpoints
    assert ("GET", "/api/payments/{id}") in endpoints
    post = endpoints[("POST", "/api/payments/authorize")]
    assert post.properties["controller"].endswith("PaymentController")
    assert post.properties["handler"] == "authorize"
    assert "PreAuthorize" in post.properties["auth"]
    assert "Valid" in post.properties["validation"]
    assert post.detection == "FRAMEWORK_INFERRED"
    # service EXPOSES endpoint, endpoint IMPLEMENTS handler
    exposes = [e for e in result.edges if e.type == "EXPOSES" and e.dst_key == post.key]
    assert exposes and exposes[0].src_key == ctx.scope("service:payment-service")


def test_kafka_producer_uses_constant_and_consumer_declaration():
    result, _ = extract()
    produced = {e.dst_key for e in result.edges if e.type == "PUBLISHES"}
    consumed = {e.dst_key for e in result.edges if e.type == "CONSUMES"}
    assert "topic:payment.completed" in produced
    assert "topic:payment.retry" in produced
    assert "topic:booking.cancelled" in consumed
    consumer_nodes = [n for n in result.nodes if n.type == "CONSUMER"]
    assert consumer_nodes and consumer_nodes[0].properties["topic"] == "booking.cancelled"


def test_feign_client_creates_cross_service_reference():
    result, _ = extract()
    refs = [e.dst_ref for e in result.edges if e.type == "DEPENDS_ON"]
    assert "service:invoice-service" in refs


def test_jpa_entity_maps_to_table_and_columns():
    result, _ = extract()
    tables = [n for n in result.nodes if n.type == "TABLE"]
    assert tables and tables[0].name == "payments"
    assert tables[0].detection == "FRAMEWORK_INFERRED"


def test_scheduled_method_becomes_background_job():
    result, _ = extract()
    jobs = [n for n in result.nodes if n.type == "BACKGROUND_JOB"]
    assert jobs and jobs[0].properties["cron"] == "0 0/5 * * * *"
    assert any(e.type == "SCHEDULES" for e in result.edges)


def test_method_signatures_and_line_numbers_are_recorded():
    result, _ = extract()
    methods = {n.name: n for n in result.nodes if n.type == "METHOD"}
    assert "authorize" in methods
    node = methods["authorize"]
    assert node.start_line and node.end_line and node.end_line > node.start_line
    assert node.properties["parameters"]
    assert node.file_path.endswith("PaymentController.java")


def test_unresolved_topic_is_not_fabricated():
    ctx = RepoContext(repository_id="r1", repository_name="svc", commit="c", root=".")
    code = """
    package x;
    @Component
    class Producer {
        private final KafkaTemplate<String,Object> kafkaTemplate;
        void send() { kafkaTemplate.send(System.getenv("TOPIC"), "1", payload); }
    }
    """
    result = JavaExtractor().extract("P.java", code, ctx)
    unresolved = [e for e in result.edges if e.type == "PUBLISHES" and not e.dst_key]
    assert unresolved and unresolved[0].properties["unresolved"] is True
    assert not [n for n in result.nodes if n.type == "KAFKA_TOPIC"]
