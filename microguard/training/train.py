"""Training pipeline for the bot detection model.

Downloads Harvard Shopping Logs dataset, extracts features,
applies heuristic labels, and trains the micrograd MLP.
"""

import json
import os
import sys
import random
from typing import List, Tuple

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from microguard.parser import parse_file, parse_string, LogEntry
from microguard.features import group_into_sessions, extract_features, Session
from microguard.labeler import label_session
from microguard.model import BotDetector


def prepare_training_data(
    log_entries: List[LogEntry],
    timeout_minutes: int = 30,
) -> Tuple[List[List[float]], List[float]]:
    """Convert log entries into training data.
    
    Args:
        log_entries: Parsed log entries
        timeout_minutes: Session timeout
    
    Returns:
        (features, labels) tuple
    """
    sessions = group_into_sessions(log_entries, timeout_minutes)
    
    features_list = []
    labels_list = []
    
    for session in sessions:
        # Skip very short sessions (< 3 requests)
        if session.request_count < 3:
            continue
        
        # Extract features
        features = extract_features(session)
        
        # Get label
        label_str, confidence, reason = label_session(session)
        label = 1.0 if label_str == 'bot' else 0.0
        
        features_list.append(features)
        labels_list.append(label)
    
    return features_list, labels_list


def train_model(
    features: List[List[float]],
    labels: List[float],
    model_path: str = "data/model.json",
    epochs: int = 100,
    learning_rate: float = 0.05,
) -> BotDetector:
    """Train the bot detection model.
    
    Args:
        features: List of feature vectors
        labels: List of labels (0.0=human, 1.0=bot)
        model_path: Path to save trained model
        epochs: Number of training epochs
        learning_rate: Learning rate
    
    Returns:
        Trained BotDetector
    """
    print(f"\n🧠 Training model...")
    print(f"   Samples: {len(features)}")
    print(f"   Bot: {sum(labels):.0f} | Human: {len(labels) - sum(labels):.0f}")
    print(f"   Epochs: {epochs} | LR: {learning_rate}")
    
    model = BotDetector()
    
    # Shuffle data
    combined = list(zip(features, labels))
    random.shuffle(combined)
    features, labels = zip(*combined)
    features = list(features)
    labels = list(labels)
    
    # Train
    losses = model.train(
        features=features,
        labels=labels,
        epochs=epochs,
        batch_size=min(32, len(features)),
        learning_rate=learning_rate,
        val_split=0.2,
        verbose=True,
    )
    
    # Save model
    os.makedirs(os.path.dirname(model_path) or '.', exist_ok=True)
    model.save(model_path)
    print(f"\n💾 Model saved to: {model_path}")
    
    # Evaluate
    print("\n📊 Final Evaluation:")
    all_preds = model.predict_batch(features)
    correct = sum(1 for pred, label in zip(all_preds, labels) 
                  if (pred > 0.5) == (label > 0.5))
    accuracy = correct / len(labels) if labels else 0.0
    print(f"   Accuracy: {accuracy:.1%}")
    
    # Confusion matrix
    tp = sum(1 for p, l in zip(all_preds, labels) if p > 0.5 and l > 0.5)
    tn = sum(1 for p, l in zip(all_preds, labels) if p <= 0.5 and l <= 0.5)
    fp = sum(1 for p, l in zip(all_preds, labels) if p > 0.5 and l <= 0.5)
    fn = sum(1 for p, l in zip(all_preds, labels) if p <= 0.5 and l > 0.5)
    
    print(f"   True Positives:  {tp} (correctly caught bots)")
    print(f"   True Negatives:  {tn} (correctly passed humans)")
    print(f"   False Positives: {fp} (humans blocked)")
    print(f"   False Negatives: {fn} (bots missed)")
    
    if tp + fp > 0:
        precision = tp / (tp + fp)
        print(f"   Precision: {precision:.1%}")
    
    if tp + fn > 0:
        recall = tp / (tp + fn)
        print(f"   Recall: {recall:.1%}")
    
    return model


def main():
    """Main training entry point."""
    # Try to load Harvard Shopping Logs dataset
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data')
    dataset_path = os.path.join(data_dir, 'access.log')
    
    if not os.path.exists(dataset_path):
        print("📥 Dataset not found. Generating synthetic training data...")
        print("   (For real training, download Harvard Shopping Logs:")
        print("    https://dataverse.harvard.edu/dataset.xhtml?persistentId=doi:10.7910/DVN/3QBYB5)")
        print()
        
        # Generate synthetic data for initial training
        features, labels = generate_synthetic_data(n_samples=500)
        print(f"   Generated {len(features)} synthetic samples")
    else:
        print(f"📂 Loading dataset from: {dataset_path}")
        entries = list(parse_file(dataset_path))
        print(f"   Parsed {len(entries)} log entries")
        
        features, labels = prepare_training_data(entries)
        print(f"   Extracted {len(features)} sessions")
    
    # Train model
    model_path = os.path.join(data_dir, 'model.json')
    model = train_model(
        features=features,
        labels=labels,
        model_path=model_path,
        epochs=100,
        learning_rate=0.05,
    )
    
    print("\n✅ Training complete!")
    print(f"   Model: {model}")
    print(f"   Ready to use: microguard scan <logfile>")


def generate_synthetic_data(n_samples: int = 500) -> Tuple[List[List[float]], List[float]]:
    """Generate synthetic training data for initial model training.
    
    Creates realistic feature vectors for bots and humans based on
    known patterns from the Nescio98 research.
    """
    features = []
    labels = []
    
    for _ in range(n_samples // 2):
        # Generate HUMAN-like session features
        # Humans: variable timing, multiple endpoints, browser UA, low error rate
        human_features = [
            random.uniform(2.0, 30.0),      # time_since_last_request (variable)
            random.uniform(1.0, 15.0),      # requests_per_minute_1m
            random.uniform(1.0, 10.0),      # requests_per_minute_5m
            random.uniform(0.4, 1.5),       # inter_request_time_cv (high = variable)
            random.uniform(30.0, 600.0),    # time_since_session_start
            random.uniform(3.0, 20.0),      # endpoint_count (multiple)
            random.uniform(1.5, 3.5),       # endpoint_sequence_entropy
            random.uniform(0.5, 1.0),       # unique_endpoint_ratio
            random.uniform(0.0, 0.1),       # method_mismatch_count
            random.uniform(0.7, 1.0),       # header_consistency_score
            1.0,                            # has_accept_language (browser)
            0.0,                            # ua_category (browser)
            random.uniform(2.0, 4.0),       # payload_entropy
            0.0,                            # field_fill_speed (N/A)
            random.uniform(1.0, 5.0),       # same_endpoint_hits
            random.uniform(0.0, 0.1),       # error_rate
            random.uniform(0.1, 0.4),       # image_ratio
            random.uniform(0.0, 0.2),       # night_ratio
            random.uniform(0.0, 2.0),       # max_sustained_click_rate
        ]
        features.append(human_features)
        labels.append(0.0)
    
    for _ in range(n_samples // 2):
        # Generate BOT-like session features
        # Bots: uniform timing, repetitive endpoints, bot UA, high error rate
        bot_type = random.choice(['scraper', 'scanner', 'crawler'])
        
        if bot_type == 'scraper':
            bot_features = [
                random.uniform(0.01, 0.1),   # time_since_last_request (very fast)
                random.uniform(50.0, 200.0), # requests_per_minute_1m (high)
                random.uniform(50.0, 200.0), # requests_per_minute_5m (high)
                random.uniform(0.0, 0.1),    # inter_request_time_cv (low = uniform)
                random.uniform(1.0, 60.0),   # time_since_session_start
                random.uniform(1.0, 5.0),    # endpoint_count (few)
                random.uniform(0.0, 1.5),    # endpoint_sequence_entropy (low)
                random.uniform(0.1, 0.5),    # unique_endpoint_ratio (low)
                random.uniform(0.0, 0.2),    # method_mismatch_count
                random.uniform(0.0, 0.3),    # header_consistency_score
                0.0,                         # has_accept_language (missing)
                1.0,                         # ua_category (bot)
                random.uniform(1.0, 3.0),    # payload_entropy
                0.0,                         # field_fill_speed (N/A)
                random.uniform(10.0, 100.0), # same_endpoint_hits (high)
                random.uniform(0.0, 0.05),   # error_rate
                random.uniform(0.0, 0.1),    # image_ratio
                random.uniform(0.0, 0.3),    # night_ratio
                random.uniform(5.0, 20.0),   # max_sustained_click_rate (high)
            ]
        elif bot_type == 'scanner':
            bot_features = [
                random.uniform(0.0, 0.05),   # time_since_last_request
                random.uniform(100.0, 500.0),# requests_per_minute_1m
                random.uniform(100.0, 500.0),# requests_per_minute_5m
                random.uniform(0.0, 0.05),   # inter_request_time_cv
                random.uniform(0.5, 10.0),   # time_since_session_start
                random.uniform(10.0, 50.0),  # endpoint_count (many)
                random.uniform(2.0, 4.0),    # endpoint_sequence_entropy
                random.uniform(0.8, 1.0),    # unique_endpoint_ratio (high)
                random.uniform(0.1, 0.5),    # method_mismatch_count
                random.uniform(0.0, 0.2),    # header_consistency_score
                0.0,                         # has_accept_language
                1.0,                         # ua_category (bot)
                random.uniform(2.0, 4.0),    # payload_entropy
                0.0,                         # field_fill_speed
                random.uniform(1.0, 3.0),    # same_endpoint_hits
                random.uniform(0.3, 0.8),    # error_rate (high - probing)
                random.uniform(0.0, 0.05),   # image_ratio
                random.uniform(0.0, 0.5),    # night_ratio (scanning at night)
                random.uniform(10.0, 50.0),  # max_sustained_click_rate
            ]
        else:  # crawler
            bot_features = [
                random.uniform(0.5, 3.0),    # time_since_last_request
                random.uniform(5.0, 30.0),   # requests_per_minute_1m
                random.uniform(5.0, 30.0),   # requests_per_minute_5m
                random.uniform(0.1, 0.3),    # inter_request_time_cv (somewhat uniform)
                random.uniform(60.0, 300.0), # time_since_session_start
                random.uniform(20.0, 100.0), # endpoint_count (many pages)
                random.uniform(2.5, 4.0),    # endpoint_sequence_entropy
                random.uniform(0.7, 1.0),    # unique_endpoint_ratio
                random.uniform(0.0, 0.1),    # method_mismatch_count
                random.uniform(0.3, 0.7),    # header_consistency_score
                0.5,                         # has_accept_language (maybe)
                1.0,                         # ua_category (bot)
                random.uniform(2.5, 3.5),    # payload_entropy
                0.0,                         # field_fill_speed
                random.uniform(1.0, 5.0),    # same_endpoint_hits
                random.uniform(0.02, 0.1),   # error_rate
                random.uniform(0.2, 0.6),    # image_ratio (crawling images)
                random.uniform(0.0, 0.1),    # night_ratio
                random.uniform(2.0, 8.0),    # max_sustained_click_rate
            ]
        
        features.append(bot_features)
        labels.append(1.0)
    
    return features, labels


if __name__ == '__main__':
    main()
