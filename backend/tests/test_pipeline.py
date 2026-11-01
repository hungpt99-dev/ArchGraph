"""Integration tests: real git repositories -> real PostgreSQL graph."""

from __future__ import annotations

import subprocess

import pytest
from sqlalchemy import select

from app.models import Edge, Node, Repository
from app.scanner.pipeline import scan_repository
from conftest import commit, make_local_repo

PAYMENT_SERVICE = {
    "pom.xml": """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.acme</groupId><artifactId>payment-service</artifactId><version>1.0</version>
</project>""",
    "src/main/resources/application.yml": """spring:
  application:
    name: payment-service
  datasource:
    url: jdbc:postgresql://postgres:5432/payments
  kafka:
    bootstrap-servers: kafka:9092
server:
  port: 8082
""",
    "src/main/java/com/acme/pay/PaymentApplication.java": """package com.acme.pay;
import org.springframework.boot.autoconfigure.SpringBootApplication;
@SpringBootApplication
public class PaymentApplication { }
""",
    "src/main/java/com/acme/pay/PaymentController.java": """package com.acme.pay;
import org.springframework.web.bind.annotation.*;
@RestController
@RequestMapping("/api/payments")
public class PaymentController {
    private final PaymentService paymentService;
    public PaymentController(PaymentService paymentService) { this.paymentService = paymentService; }
    @PostMapping("/authorize")
    public Payment authorize(@RequestBody Payment request) {
        return paymentService.authorize(request);
    }
}
""",
    "src/main/java/com/acme/pay/PaymentService.java": """package com.acme.pay;
import org.springframework.stereotype.Service;
import org.springframework.kafka.core.KafkaTemplate;
@Service
public class PaymentService {
    private static final String TOPIC = "payment.completed";
    private final KafkaTemplate<String, Object> kafkaTemplate;
    private final PaymentRepository paymentRepository;
    public PaymentService(KafkaTemplate<String, Object> kafkaTemplate, PaymentRepository paymentRepository) {
        this.kafkaTemplate = kafkaTemplate;
        this.paymentRepository = paymentRepository;
    }
    public Payment authorize(Payment payment) {
        Payment saved = paymentRepository.save(payment);
        kafkaTemplate.send(TOPIC, saved.getId().toString(), saved);
        return saved;
    }
}
""",
    "src/main/java/com/acme/pay/Payment.java": """package com.acme.pay;
import jakarta.persistence.*;
@Entity
@Table(name = "payments")
public class Payment {
    @Id private Long id;
    private String status;
}
""",
    "src/main/java/com/acme/pay/PaymentRepository.java": """package com.acme.pay;
import org.springframework.data.jpa.repository.JpaRepository;
public interface PaymentRepository extends JpaRepository<Payment, Long> { }
""",
}

INVOICE_SERVICE = {
    "pom.xml": """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.acme</groupId><artifactId>invoice-service</artifactId><version>1.0</version>
</project>""",
    "src/main/resources/application.yml": """spring:
  application:
    name: invoice-service
  datasource:
    url: jdbc:postgresql://postgres:5432/invoices
  kafka:
    bootstrap-servers: kafka:9092
server:
  port: 8084
""",
    "src/main/java/com/acme/inv/InvoiceApplication.java": """package com.acme.inv;
import org.springframework.boot.autoconfigure.SpringBootApplication;
@SpringBootApplication
public class InvoiceApplication { }
""",
    "src/main/java/com/acme/inv/InvoiceListener.java": """package com.acme.inv;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.stereotype.Component;
@Component
public class InvoiceListener {
    @KafkaListener(topics = "payment.completed", groupId = "invoice-group")
    public void onPayment(String payload) { }
}
""",
    "src/main/java/com/acme/inv/Invoice.java": """package com.acme.inv;
import jakarta.persistence.*;
@Entity
@Table(name = "invoices")
public class Invoice { @Id private Long id; private Long paymentId; }
""",
    "src/main/java/com/acme/inv/InvoiceRepository.java": """package com.acme.inv;
import org.springframework.data.jpa.repository.JpaRepository;
public interface InvoiceRepository extends JpaRepository<Invoice, Long> { }
""",
}

GATEWAY_REPO = {
    "pom.xml": """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.acme</groupId><artifactId>gateway</artifactId><version>1.0</version>
</project>""",
    "src/main/resources/application.yml": """spring:
  application:
    name: api-gateway
  cloud:
    gateway:
      routes:
        - id: payments
          uri: lb://payment-service
          predicates:
            - Path=/api/payments/**
server:
  port: 8080
""",
    "src/main/java/com/acme/gw/GatewayApplication.java": """package com.acme.gw;
import org.springframework.boot.autoconfigure.SpringBootApplication;
@SpringBootApplication
public class GatewayApplication { }
""",
}


@pytest.fixture
def scanned_workspace(db, workspace):
    repos = {}
    for name, files in (("payment-service", PAYMENT_SERVICE), ("invoice-service", INVOICE_SERVICE),
                        ("api-gateway", GATEWAY_REPO)):
        path = make_local_repo(__import__("pathlib").Path("/tmp/archgraph-test-src"), files, name)
        repo = Repository(workspace_id=workspace["id"], name=name, url=path, provider="local",
                          branch="main", default_branch="main", settings={})
        db.add(repo)
        db.commit()
        scan_repository(db, repo, trigger="test", mode="full")
        db.refresh(repo)
        repos[name] = repo
    return repos


def test_services_discovered_per_repository(db, workspace, scanned_workspace):
    services = {n.name: n for n in db.scalars(
        select(Node).where(Node.workspace_id == workspace["id"], Node.type == "SERVICE")).all()}
    assert {"payment-service", "invoice-service", "api-gateway"} <= set(services)
    assert services["payment-service"].repository_id == scanned_workspace["payment-service"].id
    assert services["payment-service"].properties["port"] == 8082
    assert services["payment-service"].detection_method == "FRAMEWORK_INFERRED"
    assert services["payment-service"].source_revision


def test_api_and_handler_chain(db, workspace, scanned_workspace):
    api = db.scalar(select(Node).where(Node.workspace_id == workspace["id"], Node.type == "API_ENDPOINT",
                                       Node.name == "POST /api/payments/authorize"))
    assert api is not None
    assert api.properties["handler"] == "authorize"
    assert api.evidence["file"].endswith("PaymentController.java")
    implements = db.scalar(select(Edge).where(Edge.src_id == api.id, Edge.type == "IMPLEMENTS"))
    handler = db.get(Node, implements.dst_id)
    assert handler.name == "authorize" and handler.type == "METHOD"
    assert handler.start_line and handler.end_line


def test_cross_repository_kafka_link(db, workspace, scanned_workspace):
    topic = db.scalar(select(Node).where(Node.workspace_id == workspace["id"], Node.type == "KAFKA_TOPIC",
                                         Node.name == "payment.completed"))
    assert topic is not None
    publishers = db.scalars(select(Edge).where(Edge.dst_id == topic.id, Edge.type == "PUBLISHES")).all()
    consumers = db.scalars(select(Edge).where(Edge.dst_id == topic.id, Edge.type == "CONSUMES")).all()
    publisher_names = {db.get(Node, e.src_id).name for e in publishers}
    consumer_names = {db.get(Node, e.src_id).name for e in consumers}
    assert "payment-service" in publisher_names
    assert "invoice-service" in consumer_names
    # the same topic node is shared by both repositories
    assert topic.repository_id is None


def test_gateway_routes_to_service(db, workspace, scanned_workspace):
    gateway = db.scalar(select(Node).where(Node.workspace_id == workspace["id"], Node.type == "SERVICE",
                                           Node.name == "api-gateway"))
    payment = db.scalar(select(Node).where(Node.workspace_id == workspace["id"], Node.type == "SERVICE",
                                           Node.name == "payment-service"))
    edge = db.scalar(select(Edge).where(Edge.src_id == gateway.id, Edge.dst_id == payment.id,
                                        Edge.type == "ROUTES_TO"))
    assert edge is not None
    assert edge.detection_method == "CONFIGURED_CONNECTION"
    assert edge.properties["paths"] == ["/api/payments/**"]


def test_business_flow_derived_from_topic(db, workspace, scanned_workspace):
    from app.models import BusinessFlow

    flow = db.scalar(select(BusinessFlow).where(BusinessFlow.workspace_id == workspace["id"],
                                                BusinessFlow.name == "Event flow: payment.completed"))
    assert flow is not None
    assert flow.detection_method == "FRAMEWORK_INFERRED"
    steps = [s["name"] for s in flow.steps]
    assert "payment-service" in steps and "invoice-service" in steps


def test_rescan_is_idempotent(db, workspace, scanned_workspace):
    from sqlalchemy import func

    keys_before = set(db.scalars(select(Node.natural_key).where(
        Node.workspace_id == workspace["id"])).all())
    edge_keys_before = set(db.scalars(select(Edge.dedupe_key).where(Edge.workspace_id == workspace["id"])).all())
    for repo_name in ("payment-service", "invoice-service", "api-gateway"):
        repo = db.get(Repository, scanned_workspace[repo_name].id)
        scan_repository(db, repo, trigger="test", mode="full")
    keys_after = set(db.scalars(select(Node.natural_key).where(
        Node.workspace_id == workspace["id"])).all())
    edge_keys_after = set(db.scalars(select(Edge.dedupe_key).where(Edge.workspace_id == workspace["id"])).all())
    assert keys_after == keys_before, f"added={keys_after - keys_before} removed={keys_before - keys_after}"
    added_edges = edge_keys_after - edge_keys_before
    detail = []
    for key in added_edges:
        e = db.scalar(select(Edge).where(Edge.dedupe_key == key))
        detail.append(f"{e.type} {e.src_id[:8]}->{(e.dst_id or e.dst_ref or '')[:8]} {e.detection_method}")
    assert not added_edges, f"added edges: {detail}"
