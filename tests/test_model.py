"""Tests for the micrograd model wrapper."""

import random

import pytest

from microguard.model import BotDetector


class TestBotDetector:
    """Tests for the BotDetector model."""
    
    def test_init(self):
        model = BotDetector()
        assert model.NUM_FEATURES == 19
        assert repr(model).startswith("BotDetector")
    
    def test_predict_random(self):
        """Random model should give roughly 50% for any input."""
        model = BotDetector()
        
        # Random features
        features = [0.5] * 19
        score = model.predict(features)
        
        # Should be between 0 and 1
        assert 0.0 <= score <= 1.0
    
    def test_predict_batch(self):
        model = BotDetector()
        batch = [[0.5] * 19 for _ in range(10)]
        scores = model.predict_batch(batch)
        assert len(scores) == 10
        assert all(0.0 <= s <= 1.0 for s in scores)
    
    def test_predict_wrong_features(self):
        model = BotDetector()
        with pytest.raises(ValueError, match="Expected 19 features"):
            model.predict([0.5] * 10)
    
    def test_save_load(self, tmp_path):
        """Test saving and loading model weights."""
        model = BotDetector()
        filepath = str(tmp_path / "test_model.json")
        
        # Save
        model.save(filepath)
        
        # Load into new model
        model2 = BotDetector(filepath)
        
        # Predictions should be identical
        features = [0.5] * 19
        assert model.predict(features) == model2.predict(features)
    
    def test_train_step(self):
        """A training step moves the model.

        Seeded, and not for tidiness. micrograd initializes weights with
        random.uniform, and on roughly 2% of inits every hidden ReLU sits at
        zero for both input patterns below. The only live gradient left is the
        output bias, and this batch is symmetric — ten targets at +1, ten at
        -1 — so that gradient cancels exactly and nothing moves. Unseeded, the
        assertion fails about 2% of the time through no fault of the code,
        which across a 12-job CI matrix is close to a coin flip per run.
        """
        random.seed(1234)
        model = BotDetector()
        
        # Simple training data
        features = [[1.0] * 19] * 10 + [[0.0] * 19] * 10
        labels = [1.0] * 10 + [0.0] * 10
        
        # Get initial predictions
        initial_scores = model.predict_batch(features)
        
        # Train
        model.train_step(features, labels, learning_rate=0.1)
        
        # Predictions should have changed
        new_scores = model.predict_batch(features)
        assert initial_scores != new_scores
    
    def test_sigmoid(self):
        """Test sigmoid function."""
        assert BotDetector.sigmoid(0) == 0.5
        assert BotDetector.sigmoid(100) > 0.99
        assert BotDetector.sigmoid(-100) < 0.01
        assert 0.0 < BotDetector.sigmoid(1) < 1.0
