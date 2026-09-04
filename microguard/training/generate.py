"""Generate realistic synthetic training data for bot detection.

Based on patterns from:
- Nescio98 research (19 HTTP-level features)
- Real bot behavior (scrapers, scanners, crawlers)
- Real human behavior (browsing, shopping, researching)
"""

import json
import os
import random


def generate_human_session() -> list[float]:
    """Generate feature vector for a realistic human session.
    
    Humans exhibit:
    - Variable timing (high CV)
    - Multiple endpoints explored
    - Browser user agent
    - Low error rate
    - Some image requests
    - Referrer present
    """
    # Decide session type
    session_type = random.choices(
        ['browser', 'shopper', 'researcher'],
        weights=[0.5, 0.3, 0.2]
    )[0]
    
    if session_type == 'browser':
        return [
            random.uniform(3.0, 20.0),      # time_since_last_request
            random.uniform(1.0, 8.0),       # requests_per_minute_1m
            random.uniform(1.0, 6.0),       # requests_per_minute_5m
            random.uniform(0.5, 1.8),       # inter_request_time_cv (high = variable)
            random.uniform(60.0, 600.0),    # time_since_session_start
            random.uniform(3.0, 12.0),      # endpoint_count
            random.uniform(1.5, 3.2),       # endpoint_sequence_entropy
            random.uniform(0.5, 1.0),       # unique_endpoint_ratio
            random.uniform(0.0, 0.05),      # method_mismatch_count
            random.uniform(0.8, 1.0),       # header_consistency_score
            1.0,                            # has_accept_language
            0.0,                            # ua_category (browser)
            random.uniform(2.2, 3.8),       # payload_entropy
            0.0,                            # field_fill_speed (N/A)
            random.uniform(1.0, 4.0),       # same_endpoint_hits
            random.uniform(0.0, 0.08),      # error_rate
            random.uniform(0.15, 0.45),     # image_ratio
            random.uniform(0.0, 0.15),      # night_ratio
            random.uniform(0.0, 2.5),       # max_sustained_click_rate
        ]
    elif session_type == 'shopper':
        return [
            random.uniform(5.0, 30.0),      # time_since_last_request (browsing)
            random.uniform(0.5, 5.0),       # requests_per_minute_1m
            random.uniform(0.5, 4.0),       # requests_per_minute_5m
            random.uniform(0.6, 2.0),       # inter_request_time_cv
            random.uniform(120.0, 1800.0),  # time_since_session_start (long)
            random.uniform(5.0, 20.0),      # endpoint_count (many products)
            random.uniform(2.0, 3.5),       # endpoint_sequence_entropy
            random.uniform(0.6, 1.0),       # unique_endpoint_ratio
            random.uniform(0.0, 0.1),       # method_mismatch_count
            random.uniform(0.85, 1.0),      # header_consistency_score
            1.0,                            # has_accept_language
            0.0,                            # ua_category
            random.uniform(2.5, 3.5),       # payload_entropy
            0.0,                            # field_fill_speed
            random.uniform(1.0, 3.0),       # same_endpoint_hits
            random.uniform(0.0, 0.05),      # error_rate
            random.uniform(0.3, 0.6),       # image_ratio (product images)
            random.uniform(0.0, 0.1),       # night_ratio
            random.uniform(0.0, 1.5),       # max_sustained_click_rate
        ]
    else:  # researcher
        return [
            random.uniform(2.0, 15.0),      # time_since_last_request
            random.uniform(2.0, 12.0),      # requests_per_minute_1m
            random.uniform(2.0, 10.0),      # requests_per_minute_5m
            random.uniform(0.4, 1.2),       # inter_request_time_cv
            random.uniform(60.0, 300.0),    # time_since_session_start
            random.uniform(8.0, 30.0),      # endpoint_count (many pages)
            random.uniform(2.5, 4.0),       # endpoint_sequence_entropy (high)
            random.uniform(0.7, 1.0),       # unique_endpoint_ratio
            random.uniform(0.0, 0.02),      # method_mismatch_count
            random.uniform(0.9, 1.0),       # header_consistency_score
            1.0,                            # has_accept_language
            0.0,                            # ua_category
            random.uniform(2.0, 3.0),       # payload_entropy
            0.0,                            # field_fill_speed
            random.uniform(1.0, 3.0),       # same_endpoint_hits
            random.uniform(0.0, 0.03),      # error_rate
            random.uniform(0.05, 0.2),      # image_ratio
            random.uniform(0.0, 0.1),       # night_ratio
            random.uniform(0.5, 3.0),       # max_sustained_click_rate
        ]


def generate_bot_session() -> list[float]:
    """Generate feature vector for a realistic bot session.
    
    Bots exhibit:
    - Uniform timing (low CV)
    - Repetitive endpoints
    - Bot user agent
    - High request rate
    - Low header consistency
    """
    # Decide bot type
    bot_type = random.choices(
        ['scraper', 'scanner', 'crawler', 'api_abuse'],
        weights=[0.35, 0.25, 0.25, 0.15]
    )[0]
    
    if bot_type == 'scraper':
        # Price scraper: fast, uniform, hits same endpoints
        return [
            random.uniform(0.02, 0.15),     # time_since_last_request (very fast)
            random.uniform(60.0, 200.0),    # requests_per_minute_1m
            random.uniform(60.0, 200.0),    # requests_per_minute_5m
            random.uniform(0.0, 0.08),      # inter_request_time_cv (uniform)
            random.uniform(2.0, 30.0),      # time_since_session_start
            random.uniform(1.0, 4.0),       # endpoint_count (few)
            random.uniform(0.3, 1.2),       # endpoint_sequence_entropy (low)
            random.uniform(0.05, 0.3),      # unique_endpoint_ratio (low)
            random.uniform(0.0, 0.1),       # method_mismatch_count
            random.uniform(0.0, 0.3),       # header_consistency_score (low)
            0.0,                            # has_accept_language (missing)
            1.0,                            # ua_category (bot)
            random.uniform(1.5, 2.5),       # payload_entropy
            0.0,                            # field_fill_speed
            random.uniform(15.0, 100.0),    # same_endpoint_hits (high)
            random.uniform(0.0, 0.03),      # error_rate
            random.uniform(0.0, 0.05),      # image_ratio
            random.uniform(0.0, 0.2),       # night_ratio
            random.uniform(8.0, 25.0),      # max_sustained_click_rate
        ]
    elif bot_type == 'scanner':
        # Vulnerability scanner: fast, many endpoints, high errors
        return [
            random.uniform(0.01, 0.08),     # time_since_last_request
            random.uniform(100.0, 500.0),   # requests_per_minute_1m
            random.uniform(100.0, 500.0),   # requests_per_minute_5m
            random.uniform(0.0, 0.05),      # inter_request_time_cv
            random.uniform(0.5, 10.0),      # time_since_session_start
            random.uniform(15.0, 80.0),     # endpoint_count (many)
            random.uniform(2.5, 4.5),       # endpoint_sequence_entropy
            random.uniform(0.8, 1.0),       # unique_endpoint_ratio (high)
            random.uniform(0.1, 0.6),       # method_mismatch_count
            random.uniform(0.0, 0.2),       # header_consistency_score
            0.0,                            # has_accept_language
            1.0,                            # ua_category
            random.uniform(2.0, 4.0),       # payload_entropy
            0.0,                            # field_fill_speed
            random.uniform(1.0, 3.0),       # same_endpoint_hits
            random.uniform(0.2, 0.8),       # error_rate (high - probing)
            random.uniform(0.0, 0.03),      # image_ratio
            random.uniform(0.1, 0.5),       # night_ratio (scanning at night)
            random.uniform(15.0, 60.0),     # max_sustained_click_rate
        ]
    elif bot_type == 'crawler':
        # Web crawler: moderate speed, systematic, many pages
        return [
            random.uniform(0.3, 2.0),       # time_since_last_request
            random.uniform(5.0, 25.0),      # requests_per_minute_1m
            random.uniform(5.0, 25.0),      # requests_per_minute_5m
            random.uniform(0.05, 0.25),     # inter_request_time_cv (somewhat uniform)
            random.uniform(60.0, 600.0),    # time_since_session_start
            random.uniform(20.0, 100.0),    # endpoint_count (many pages)
            random.uniform(3.0, 4.5),       # endpoint_sequence_entropy
            random.uniform(0.7, 1.0),       # unique_endpoint_ratio
            random.uniform(0.0, 0.05),      # method_mismatch_count
            random.uniform(0.2, 0.6),       # header_consistency_score
            0.3,                            # has_accept_language (maybe)
            1.0,                            # ua_category
            random.uniform(2.5, 3.5),       # payload_entropy
            0.0,                            # field_fill_speed
            random.uniform(1.0, 5.0),       # same_endpoint_hits
            random.uniform(0.01, 0.08),     # error_rate
            random.uniform(0.2, 0.5),       # image_ratio (crawling images)
            random.uniform(0.0, 0.1),       # night_ratio
            random.uniform(2.0, 10.0),      # max_sustained_click_rate
        ]
    else:  # api_abuse
        # API abuse: fast, targeted, high volume
        return [
            random.uniform(0.01, 0.1),      # time_since_last_request
            random.uniform(80.0, 300.0),    # requests_per_minute_1m
            random.uniform(80.0, 300.0),    # requests_per_minute_5m
            random.uniform(0.0, 0.06),      # inter_request_time_cv
            random.uniform(1.0, 20.0),      # time_since_session_start
            random.uniform(1.0, 3.0),       # endpoint_count (targeted)
            random.uniform(0.2, 1.0),       # endpoint_sequence_entropy (low)
            random.uniform(0.1, 0.4),       # unique_endpoint_ratio
            random.uniform(0.0, 0.2),       # method_mismatch_count
            random.uniform(0.0, 0.25),      # header_consistency_score
            0.0,                            # has_accept_language
            1.0,                            # ua_category
            random.uniform(3.0, 5.0),       # payload_entropy (random data)
            0.0,                            # field_fill_speed
            random.uniform(10.0, 80.0),     # same_endpoint_hits (high)
            random.uniform(0.0, 0.1),       # error_rate
            random.uniform(0.0, 0.02),      # image_ratio
            random.uniform(0.0, 0.3),       # night_ratio
            random.uniform(10.0, 40.0),     # max_sustained_click_rate
        ]


def generate_dataset(
    n_samples: int = 2000,
    balance: float = 0.5,
    seed: int = 42,
) -> tuple[list[list[float]], list[float]]:
    """Generate a balanced dataset of human and bot sessions.
    
    Args:
        n_samples: Total number of samples
        balance: Fraction that are human (0.0 to 1.0)
        seed: Random seed
    
    Returns:
        (features, labels) tuple
    """
    random.seed(seed)
    
    n_human = int(n_samples * balance)
    n_bot = n_samples - n_human
    
    features = []
    labels = []
    
    for _ in range(n_human):
        features.append(generate_human_session())
        labels.append(0.0)
    
    for _ in range(n_bot):
        features.append(generate_bot_session())
        labels.append(1.0)
    
    # Shuffle
    combined = list(zip(features, labels))
    random.shuffle(combined)
    shuffled_features, shuffled_labels = zip(*combined)

    return list(shuffled_features), list(shuffled_labels)


def save_dataset(
    features: list[list[float]],
    labels: list[float],
    filepath: str,
):
    """Save dataset to JSON file."""
    data = {
        'features': features,
        'labels': labels,
        'n_samples': len(features),
        'n_features': len(features[0]) if features else 0,
        'n_human': sum(1 for l in labels if l == 0.0),
        'n_bot': sum(1 for l in labels if l == 1.0),
    }
    
    with open(filepath, 'w') as f:
        json.dump(data, f, indent=2)
    
    print(f"💾 Dataset saved to: {filepath}")
    print(f"   Samples: {data['n_samples']}")
    print(f"   Human: {data['n_human']} | Bot: {data['n_bot']}")
    print(f"   Features: {data['n_features']}")


if __name__ == '__main__':
    # Generate dataset
    features, labels = generate_dataset(n_samples=2000, balance=0.5)
    
    # Save
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    os.makedirs(data_dir, exist_ok=True)
    save_dataset(features, labels, os.path.join(data_dir, 'training_data.json'))
