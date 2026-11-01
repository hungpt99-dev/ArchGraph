"""Extractor tests for TypeScript, Python, Go, SQL and configuration parsing."""

from __future__ import annotations

from app.scanner.base import RepoContext
from app.scanner.config_parser import ConfigParser, parse_db_url
from app.scanner.go import GoExtractor
from app.scanner.python_lang import PythonExtractor
from app.scanner.sql import SqlExtractor
from app.scanner.typescript import TypeScriptExtractor


def ctx(**kwargs):
    return RepoContext(repository_id="r1", repository_name="svc", commit="abc", root=".", **kwargs)


def test_nestjs_controller_routes_and_kafka():
    code = """
import { Controller, Get, Post, Body } from "@nestjs/common";
import { Kafka } from "kafkajs";

@Controller("invoices")
export class InvoiceController {
  constructor(private readonly service: InvoiceService) {}

  @Get(":id")
  async findOne(@Param("id") id: string) {
    return this.service.findOne(id);
  }

  @Post()
  async create(@Body() dto: CreateInvoiceDto) {
    return this.service.create(dto);
  }
}
"""
    result = TypeScriptExtractor().extract("src/invoice.controller.ts", code, ctx(npm_deps={"_name": "invoices-api"}))
    endpoints = {(n.properties["http_method"], n.properties["path"]) for n in result.nodes if n.type == "API_ENDPOINT"}
    assert ("GET", "/invoices/:id") in endpoints
    assert ("POST", "/invoices") in endpoints


def test_kafkajs_produce_and_consume():
    code = """
const producer = kafka.producer();
await producer.send({ topic: "payment.completed", messages: [{ value: "x" }] });
await consumer.subscribe({ topic: "booking.cancelled", fromBeginning: false });
"""
    result = TypeScriptExtractor().extract("src/kafka.ts", code, ctx())
    published = {e.dst_key for e in result.edges if e.type == "PUBLISHES"}
    consumed = {e.dst_key for e in result.edges if e.type == "CONSUMES"}
    assert published == {"topic:payment.completed"}
    assert consumed == {"topic:booking.cancelled"}


def test_fastapi_routes_from_ast():
    code = '''
from fastapi import FastAPI, APIRouter

app = FastAPI()
router = APIRouter(prefix="/api/rooms")

@app.get("/health")
def health():
    return {"ok": True}

@router.post("/{room_id}/reserve")
async def reserve(room_id: int):
    return {"room": room_id}
'''
    result = PythonExtractor().extract("service/main.py", code, ctx())
    paths = {(n.properties["http_method"], n.properties["path"]) for n in result.nodes if n.type == "API_ENDPOINT"}
    assert ("GET", "/health") in paths
    assert ("POST", "/api/rooms/{room_id}/reserve") in paths
    assert any(n.type == "SERVICE" for n in result.nodes)


def test_sqlalchemy_model_table():
    code = '''
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass

class Invoice(Base):
    __tablename__ = "invoices"
'''
    result = PythonExtractor().extract("models.py", code, ctx())
    tables = [n for n in result.nodes if n.type == "TABLE"]
    assert tables and tables[0].name == "invoices"
    assert any(e.type == "RELATED_TO" for e in result.edges)


def test_go_http_routes():
    code = '''
package main

import "github.com/gin-gonic/gin"

func main() {
    r := gin.Default()
    r.GET("/api/rooms/:id", getRoom)
    r.POST("/api/rooms", createRoom)
}

func getRoom(c *gin.Context) {}
'''
    result = GoExtractor().extract("main.go", code, ctx())
    paths = {n.properties["path"] for n in result.nodes if n.type == "API_ENDPOINT"}
    assert "/api/rooms/:id" in paths and "/api/rooms" in paths


def test_sql_migration_tables_and_columns():
    sql = """
CREATE TABLE payments (
    id BIGSERIAL PRIMARY KEY,
    reservation_id BIGINT NOT NULL,
    amount NUMERIC(12,2) NOT NULL,
    status VARCHAR(32) NOT NULL
);
ALTER TABLE payments ADD COLUMN reference VARCHAR(64);
"""
    result = SqlExtractor().extract("db/V1__init.sql", sql, ctx())
    tables = [n for n in result.nodes if n.type == "TABLE"]
    columns = {n.name for n in result.nodes if n.type == "COLUMN"}
    assert tables and tables[0].name == "payments"
    assert {"id", "amount", "status", "reference"} <= columns


def test_spring_yaml_produces_service_db_and_gateway_context():
    yaml_text = """
spring:
  application:
    name: reservation-service
  datasource:
    url: jdbc:postgresql://postgres:5432/booking
  kafka:
    bootstrap-servers: kafka:9092
  cloud:
    gateway:
      routes:
        - id: payment-route
          uri: lb://payment-service
          predicates:
            - Path=/api/payments/**
server:
  port: 8083
"""
    parser = ConfigParser()
    context = ctx()
    result = parser.parse("reservation-service/src/main/resources/application.yml", yaml_text, context)
    assert context.service_hints[0]["name"] == "reservation-service"
    assert context.service_hints[0]["port"] == 8083
    assert context.jdbc_urls == ["jdbc:postgresql://postgres:5432/booking"]
    assert context.gateway_routes[0]["uri"] == "lb://payment-service"
    databases = [n for n in result.nodes if n.type == "DATABASE"]
    assert databases and databases[0].name == "booking"
    assert any(e.type == "DEPENDS_ON" for e in result.edges)


def test_pom_modules_and_shared_artifact_dependency():
    pom = """<project xmlns="http://maven.apache.org/POM/4.0.0">
  <groupId>com.acme</groupId><artifactId>platform</artifactId><version>1.0</version>
  <packaging>pom</packaging>
  <modules><module>payments</module><module>invoices</module></modules>
  <dependencies>
    <dependency><groupId>com.acme</groupId><artifactId>shared-lib</artifactId><version>1.0</version></dependency>
  </dependencies>
</project>"""
    parser = ConfigParser()
    context = ctx()
    result = parser.parse("pom.xml", pom, context)
    modules = {n.name for n in result.nodes if n.type == "MODULE"}
    assert {"platform", "payments", "invoices"} <= modules
    refs = [e.dst_ref for e in result.edges if e.type == "DECLARED_DEPENDENCY"]
    assert "artifact:com.acme:shared-lib" in refs


def test_database_url_parsing_and_secret_redaction():
    parsed = parse_db_url("jdbc:postgresql://db.internal:5433/payments?password=hunter2")
    assert parsed["engine"] == "postgres"
    assert parsed["host"] == "db.internal" and parsed["port"] == 5433
    assert "hunter2" not in parsed["url_redacted"]
