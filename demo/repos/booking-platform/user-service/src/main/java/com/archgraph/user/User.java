package com.archgraph.user;

import jakarta.persistence.*;

@Entity
@Table(name = "users")
public class User {
    @Id
    @GeneratedValue(strategy = GenerationType.IDENTITY)
    private Long id;
    private String email;
    private String fullName;
    private String loyaltyTier;

    public User() {}

    public User(String email, String fullName) {
        this.email = email;
        this.fullName = fullName;
        this.loyaltyTier = "STANDARD";
    }

    public Long getId() { return id; }
    public String getEmail() { return email; }
    public String getFullName() { return fullName; }
    public String getLoyaltyTier() { return loyaltyTier; }
}
