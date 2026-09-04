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


def compute_normalization(features: List[List[float]]) -> Tuple[List[float], List[float]]:
    """Compute min/max normalization parameters from feature vectors.
    
    Args:
        features: List of feature vectors
    
    Returns:
        (mins, maxs) tuple
    """
    n_feat = len(features[0])
    mins = [float('inf')] * n_feat
    maxs = [float('-inf')] * n_feat
    
    for row in features:
        for i, v in enumerate(row):
            if v < mins[i]:
                mins[i] = v
            if v > maxs[i]:
                maxs[i] = v
    
    return mins, maxs


def normalize_features(
    features: List[List[float]],
    mins: List[float],
    maxs: List[float],
) -> List[List[float]]:
    """Normalize features to [0, 1] range.
    
    Features with zero range (min == max) map to 0.5.
    """
    normalized = []
    for row in features:
        norm_row = []
        for i, v in enumerate(row):
            if maxs[i] > mins[i]:
                norm_row.append((v - mins[i]) / (maxs[i] - mins[i]))
            else:
                norm_row.append(0.5)  # Zero-range feature
        normalized.append(norm_row)
    return normalized


def train_model(
    features: List[List[float]],
    labels: List[float],
    model_path: str = "data/model.json",
    epochs: int = 100,
    learning_rate: float = 0.05,
) -> BotDetector:
    """Train the bot detection model with normalization.
    
    Computes normalization from training data, normalizes features,
    saves normalization.json alongside model.json.
    
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
    
    # Compute normalization from training data
    mins, maxs = compute_normalization(features)
    
    # Save normalization params
    norm_path = os.path.join(os.path.dirname(model_path) or '.', 'normalization.json')
    os.makedirs(os.path.dirname(norm_path) or '.', exist_ok=True)
    with open(norm_path, 'w') as f:
        json.dump({'mins': mins, 'maxs': maxs}, f, indent=2)
    print(f"   Normalization saved to: {norm_path}")
    
    # Keep raw features and labels for evaluation (predict() normalizes internally)
    raw_features = features[:]
    raw_labels = labels[:]
    
    # Normalize features for training
    features = normalize_features(features, mins, maxs)
    
    model = BotDetector()
    # Disable normalization in predict() — features are already normalized
    model.norm_mins = None
    model.norm_maxs = None
    
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
    
    # Restore normalization params so predict() works at inference time
    model.norm_mins = mins
    model.norm_maxs = maxs
    
    # Save model
    os.makedirs(os.path.dirname(model_path) or '.', exist_ok=True)
    model.save(model_path)
    print(f"\n💾 Model saved to: {model_path}")
    
    # Evaluate (predict_batch normalizes internally using restored params)
    print("\n📊 Final Evaluation:")
    all_preds = model.predict_batch(raw_features)
    correct = sum(1 for pred, label in zip(all_preds, raw_labels) 
                  if (pred > 0.5) == (label > 0.5))
    accuracy = correct / len(labels) if labels else 0.0
    print(f"   Accuracy: {accuracy:.1%}")
    
    # Confusion matrix
    tp = sum(1 for p, l in zip(all_preds, raw_labels) if p > 0.5 and l > 0.5)
    tn = sum(1 for p, l in zip(all_preds, raw_labels) if p <= 0.5 and l <= 0.5)
    fp = sum(1 for p, l in zip(all_preds, raw_labels) if p > 0.5 and l <= 0.5)
    fn = sum(1 for p, l in zip(all_preds, raw_labels) if p <= 0.5 and l > 0.5)
    
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
    data_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))), 'data')
    
    # Priority 1: Harvard training data (pre-processed from Dataverse)
    harvard_path = os.path.join(data_dir, 'harvard_training_data.json')
    if os.path.exists(harvard_path):
        print(f"📂 Loading Harvard training data from: {harvard_path}")
        with open(harvard_path) as f:
            data = json.load(f)
        features = data['features']
        labels = data['labels']
        print(f"   Samples: {len(features)} (Human: {data.get('n_human', '?')}, Bot: {data.get('n_bot', '?')})")
    
    # Priority 2: Real training data (Zenodo + synthetic combined)
    elif os.path.exists(os.path.join(data_dir, 'real_training_data.json')):
        real_path = os.path.join(data_dir, 'real_training_data.json')
        print(f"📂 Loading real training data from: {real_path}")
        with open(real_path) as f:
            data = json.load(f)
        features = data['features']
        labels = data['labels']
        print(f"   Samples: {len(features)}")
    
    # Priority 3: Raw logs (Zenodo Apache logs)
    elif os.path.exists(os.path.join(data_dir, 'access.log')):
        dataset_path = os.path.join(data_dir, 'access.log')
        print(f"📂 Loading dataset from: {dataset_path}")
        entries = list(parse_file(dataset_path))
        print(f"   Parsed {len(entries)} log entries")
        features, labels = prepare_training_data(entries)
        print(f"   Extracted {len(features)} sessions")
    
    # Priority 4: Synthetic fallback
    else:
        print("📥 No dataset found. Generating synthetic training data...")
        features, labels = generate_synthetic_data(n_samples=500)
        print(f"   Generated {len(features)} synthetic samples")
    
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
