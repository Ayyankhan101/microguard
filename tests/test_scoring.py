"""Direct tests for the shared heuristic/model score blend.

`compute_combined_score` is the one place the blend lives (extracted from
cli.py and watch.py, which had drifted out of sync — see CHANGELOG). Until
now it was only covered transitively through scan/watch/live callers, so a
change to the formula could pass every existing test while silently altering
every blocking decision in the product. These are the direct tests.

    combined = 0.6 * model + 0.4 * confidence
      label 'bot'   -> floored at confidence      (a strong rule is not
                                                   diluted by a weak model)
      label 'human' -> capped at 1.0 - confidence (a strong rule is not
                                                   overridden by the model)
      anything else -> raw blend, no clamp
"""

import pytest

from microguard.scoring import compute_combined_score


class TestBotFloor:
    def test_confident_bot_rule_survives_a_silent_model(self):
        """The exact shape of the live path before the model loads."""
        # blend = 0.6*0.0 + 0.4*0.95 = 0.38, floored up to the rule's own 0.95
        assert compute_combined_score('bot', 0.95, 0.0) == pytest.approx(0.95)

    def test_blend_wins_when_it_already_exceeds_the_floor(self):
        # blend = 0.6*1.0 + 0.4*0.5 = 0.80 > 0.5, so the floor does nothing
        assert compute_combined_score('bot', 0.5, 1.0) == pytest.approx(0.80)

    def test_floor_is_exactly_the_confidence_not_above_it(self):
        assert compute_combined_score('bot', 0.90, 0.0) == pytest.approx(0.90)


class TestHumanCap:
    def test_confident_human_rule_survives_a_loud_model(self):
        """The score-blending asymmetry bug this function was extracted to fix.

        A model screaming 1.0 must not override a heuristic that correctly
        recognizes e.g. a single-endpoint GraphQL session as human.
        """
        # blend = 0.6*1.0 + 0.4*0.9 = 0.96, capped down to 1.0 - 0.9 = 0.10
        assert compute_combined_score('human', 0.9, 1.0) == pytest.approx(0.10)

    def test_blend_wins_when_it_is_already_under_the_cap(self):
        # blend = 0.6*0.0 + 0.4*0.1 = 0.04 < 1.0 - 0.1 = 0.90
        assert compute_combined_score('human', 0.1, 0.0) == pytest.approx(0.04)

    def test_capped_human_lands_below_every_shipped_block_threshold(self):
        """0.85 is the live default, 0.7 is the spec's. Neither may fire here."""
        score = compute_combined_score('human', 0.95, 1.0)
        assert score < 0.7
        assert score < 0.85


class TestUnclampedLabels:
    def test_automated_integration_gets_the_raw_blend(self):
        # neither branch applies: 0.6*0.5 + 0.4*0.9 = 0.66
        assert compute_combined_score('automated-integration', 0.9, 0.5) == pytest.approx(0.66)

    def test_unknown_label_gets_the_raw_blend(self):
        assert compute_combined_score('something-new', 0.5, 0.5) == pytest.approx(0.5)


class TestBoundaries:
    def test_all_zero(self):
        assert compute_combined_score('human', 0.0, 0.0) == pytest.approx(0.0)

    def test_certain_bot_certain_model(self):
        assert compute_combined_score('bot', 1.0, 1.0) == pytest.approx(1.0)

    def test_certain_human_is_driven_to_zero(self):
        # cap = 1.0 - 1.0 = 0.0
        assert compute_combined_score('human', 1.0, 1.0) == pytest.approx(0.0)

    @pytest.mark.parametrize("label", ['bot', 'human', 'automated-integration'])
    @pytest.mark.parametrize("conf,model", [(0.0, 0.0), (0.5, 0.5), (1.0, 1.0), (0.0, 1.0), (1.0, 0.0)])
    def test_output_always_stays_in_range(self, label, conf, model):
        assert 0.0 <= compute_combined_score(label, conf, model) <= 1.0
