package com.archgraph.user;

public record UserResponse(Long id, String email, String fullName, String loyaltyTier) {
    public static UserResponse from(User user) {
        return new UserResponse(user.getId(), user.getEmail(), user.getFullName(), user.getLoyaltyTier());
    }
}
