package com.archgraph.payment;

import org.springframework.stereotype.Component;
import org.springframework.web.client.RestTemplate;
import java.math.BigDecimal;

@Component
public class ExternalGatewayClient {

    private static final String GATEWAY_URL = "https://api.stripe.com/v1/charges";

    private final RestTemplate restTemplate;

    public ExternalGatewayClient(RestTemplate restTemplate) {
        this.restTemplate = restTemplate;
    }

    public GatewayResult charge(BigDecimal amount, String currency) {
        // External provider call - outbound integration
        GatewayResult result = restTemplate.postForObject(GATEWAY_URL, amount, GatewayResult.class);
        return result;
    }

    public GatewayResult refund(String reference, BigDecimal amount) {
        return restTemplate.postForObject("https://api.stripe.com/v1/refunds", reference, GatewayResult.class);
    }
}
