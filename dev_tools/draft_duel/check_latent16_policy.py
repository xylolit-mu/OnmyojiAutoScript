"""16 维隐向量策略与其拟合程序的离线检查（不连接游戏）。"""

import json
import tempfile
import unittest
from itertools import combinations
from pathlib import Path

import numpy as np

from dev_tools.draft_duel import fit_latent16_policy as fit
from dev_tools.draft_duel import fit_latent_field16 as field_fit
from tasks.DraftDuel.strategy import lineup
from tasks.DraftDuel.strategy.latent16 import (LATENT_DIM, LatentPick, LatentPolicy,
                                               POLICY_FORMAT)
from tasks.DraftDuel.strategy.latent_field16 import (FIELD_FORMAT, FieldPick, FieldPolicy)


NAMES = ['甲', '乙', '丙', '丁']
VECTORS = [[1.0, 0.0], [0.0, 2.0], [1.0, 1.0], [2.0, 0.0]]
QUERY = [1.0, 1.0]
MATRIX = [[1.0, 0.0], [0.0, 1.0]]


def sample_policy():
    return LatentPolicy(NAMES, VECTORS, QUERY, MATRIX, meta={'note': '测试'})


class LatentPolicyTests(unittest.TestCase):
    def test_default_dimension_is_sixteen(self):
        self.assertEqual(LATENT_DIM, 16)

    def test_score_is_self_strength_plus_mean_pair_score(self):
        policy = sample_policy()
        # 候选"甲"自己 q·z = 1，与"丙"配对 z·M·z = 1 -> 2.0
        scores = policy.score(['甲'], team=['丙'])
        self.assertAlmostEqual(float(scores[0]), 2.0)
        # 候选"乙"自己 q·z = 2，与"丙"配对 0*1 + 2*1 = 2 -> 4.0
        scores = policy.score(['乙'], team=['丙'])
        self.assertAlmostEqual(float(scores[0]), 4.0)
        # 两名队友取平均：甲·丙 = 1、甲·丁 = 2 -> 自身 1.0 + 平均 1.5 = 2.5
        scores = policy.score(['甲'], team=['丙', '丁'])
        self.assertAlmostEqual(float(scores[0]), 2.5)

    def test_first_round_only_uses_self_strength(self):
        policy = sample_policy()
        offers = ['甲', '乙', '丙']
        self.assertAlmostEqual(policy.strength('乙'), 2.0)
        pick = policy.choose(offers)
        self.assertIsInstance(pick, LatentPick)
        self.assertEqual(pick.name, '乙')  # 只有 q·z：乙 2.0 最高
        scores = policy.score(offers)
        self.assertEqual(list(np.round(scores, 6)), [1.0, 2.0, 2.0])

    def test_team_context_can_change_the_pick(self):
        policy = sample_policy()
        self.assertEqual(policy.choose(['甲', '丙']).name, '丙')
        # 与"乙、丁"搭档时甲只加 q·z 的一半，丙加 1.5 的一半，仍按均值比较
        self.assertEqual(policy.choose(['甲', '丙'], team=['乙', '丁']).name, '丙')
        # 换一组参数让般配分压过自身强度：甲自身 1 但和丙配对 +5，乙自身 2 但配对 0
        policy = LatentPolicy(NAMES, VECTORS, [1.0, 1.0], [[0.0, 5.0], [0.0, 0.0]])
        self.assertEqual(policy.choose(['甲', '乙']).name, '乙')
        self.assertEqual(policy.choose(['甲', '乙'], team=['丙']).name, '甲')

    def test_ties_keep_candidate_order(self):
        policy = LatentPolicy(['甲', '乙'], [[0.0, 0.0], [0.0, 0.0]], [1.0, 0.0],
                              [[0.0, 0.0], [0.0, 0.0]])
        self.assertEqual(policy.choose(['甲', '乙']).name, '甲')
        self.assertEqual(policy.choose(['乙', '甲']).name, '乙')

    def test_unknown_candidate_scores_zero_and_is_reported(self):
        policy = sample_policy()
        scores = policy.score(['未知卡', '甲'], team=['不存在'])
        self.assertEqual(float(scores[0]), 0.0)
        self.assertAlmostEqual(float(scores[1]), 1.0)
        pick = policy.choose(['未知卡'])
        self.assertTrue(any('未收录' in reason for reason in pick.reasons))

    def test_explain_lists_self_and_each_partner(self):
        policy = sample_policy()
        reasons = policy.explain('甲', team=['丙', '丁'])
        self.assertEqual(len(reasons), 3)
        self.assertIn('自身强度 +1.00', reasons[0])
        self.assertIn('与丙般配 +1.00', reasons[1])

    def test_json_roundtrip(self):
        policy = sample_policy()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'nested' / 'latent16.json'
            policy.save(path)
            loaded = LatentPolicy.load(path)
            payload = json.loads(path.read_text(encoding='utf-8'))
        self.assertEqual(payload['format'], POLICY_FORMAT)
        self.assertEqual(payload['dim'], 2)
        self.assertEqual(loaded.names, policy.names)
        self.assertTrue(np.allclose(loaded.vectors, policy.vectors))
        self.assertTrue(np.allclose(loaded.query, policy.query))
        self.assertTrue(np.allclose(loaded.matrix, policy.matrix))
        self.assertEqual(loaded.meta, {'note': '测试'})

    def test_shape_and_format_validation(self):
        with self.assertRaises(ValueError):
            LatentPolicy(NAMES, VECTORS[:2], QUERY, MATRIX)
        with self.assertRaises(ValueError):
            LatentPolicy(NAMES, VECTORS, [1.0], MATRIX)
        with self.assertRaises(ValueError):
            LatentPolicy(NAMES, VECTORS, QUERY, [[1.0]])
        with self.assertRaises(ValueError):
            LatentPolicy.from_dict({'format': 'other', 'names': NAMES})
        with self.assertRaises(ValueError):
            LatentPolicy(['甲', '甲'], [[0.0, 0.0]] * 2, [0.0, 0.0], [[0.0, 0.0]] * 2)
        with self.assertRaises(ValueError):
            LatentPolicy(NAMES, VECTORS, None, MATRIX, head='mlp')
        with self.assertRaises(ValueError):
            LatentPolicy(NAMES, VECTORS, None, MATRIX, head='mlp',
                         head_params={'W1': [[0.0]]})
        with self.assertRaises(ValueError):
            LatentPolicy(NAMES, VECTORS, None, MATRIX, head='role',
                         head_params={'W1': [[0.0]]})


class RoleHeadTests(unittest.TestCase):
    @staticmethod
    def build(seed=2):
        names = sorted(lineup.CATALOG)[:12]
        index = {name: row for row, name in enumerate(names)}
        rng = np.random.default_rng(seed)
        codes = (rng.random((len(names), len(fit.ROLE_NAMES))) > 0.6).astype(float)
        net = fit.RoleNet(len(names), 4, roles=codes, hidden=8, seed=0)
        return names, index, codes, net

    @staticmethod
    def data(index, seed=0, count=40):
        rng = np.random.default_rng(seed)
        names = list(index)
        rows = []
        for number in range(count):
            offers = [str(name) for name in rng.choice(names, size=3, replace=False)]
            pool = [name for name in names if name not in offers]
            team = [str(name) for name in rng.choice(pool, size=2, replace=False)]
            rows.append({'episode': number, 'round': 1, 'offers': offers, 'team': team,
                         'pick': offers[0], 'picked_index': 0})
        return fit.SplitData(rows, index, team_size=2)

    def test_role_forward_matches_net(self):
        names, index, codes, net = self.build()
        policy = LatentPolicy(names, net.Z, None, net.matrix, head='role',
                              head_params={'W1': net.W1, 'b1': net.b1,
                                           'W2': net.W2, 'b2': net.b2}, roles=codes)
        offers, team = names[:3], names[3:6]
        expected = net.scores(np.array([[index[name] for name in offers]]),
                              np.array([[index[name] for name in team]]),
                              np.array([len(team)]))[0]
        self.assertTrue(np.allclose(policy.score(offers, team), expected, atol=1e-9))

    def test_role_backprop_matches_finite_difference(self):
        _, index, _, net = self.build()
        data = self.data(index)
        errors = fit.gradient_check(net, data, samples=3, weight_decay=0.01)
        self.assertLess(max(errors.values()), 1e-6, errors)
        self.assertEqual(set(errors), {'Z', 'matrix', 'W1', 'b1', 'W2', 'b2'})

    def test_role_json_roundtrip(self):
        names, index, codes, net = self.build()
        policy = LatentPolicy(names, net.Z, None, net.matrix, head='role',
                              head_params={'W1': net.W1, 'b1': net.b1,
                                           'W2': net.W2, 'b2': net.b2}, roles=codes)
        with tempfile.TemporaryDirectory() as directory:
            path = policy.save(Path(directory) / 'latent16_role.json')
            payload = json.loads(path.read_text(encoding='utf-8'))
            loaded = LatentPolicy.load(path)
        self.assertEqual(payload['head'], 'role')
        self.assertIn('roles', payload)
        offers, team = names[:3], names[3:6]
        self.assertTrue(np.allclose(loaded.score(offers, team),
                                    policy.score(offers, team), atol=1e-5))


class ShallowHeadPolicyTests(unittest.TestCase):
    @staticmethod
    def build():
        rng = np.random.default_rng(3)
        dim = 4
        vectors = rng.normal(size=(len(NAMES), dim))
        matrix = rng.normal(size=(dim, dim))
        params = {'W1': rng.normal(size=(3 * dim + 2, 8)), 'b1': rng.normal(size=8),
                  'W2': rng.normal(size=8), 'b2': rng.normal(size=1)}
        return LatentPolicy(NAMES, vectors, None, matrix, head='mlp',
                            head_params=params), vectors, matrix, params

    def test_forward_matches_manual_features(self):
        policy, vectors, matrix, params = self.build()
        team = ['丙', '丁']
        offers = ['甲', '乙']
        scores = policy.score(offers, team)
        zc = vectors[[0, 1]]
        zt = vectors[[2, 3]]
        mean = zt.mean(axis=0)
        pair = (zc @ matrix @ zt.T).mean(axis=1)
        features = np.concatenate([
            zc, np.tile(mean, (2, 1)), zc * mean, pair[:, None],
            np.full((2, 1), len(team) / 4),
        ], axis=1)
        hidden = np.maximum(features @ params['W1'] + params['b1'], 0.0)
        expected = hidden @ params['W2'] + params['b2'][0]
        self.assertTrue(np.allclose(scores, expected, atol=1e-9))

    def test_empty_team_is_self_strength(self):
        policy, _, _, _ = self.build()
        self.assertAlmostEqual(policy.strength('甲'),
                               float(policy.score(['甲'], [])[0]))
        pick = policy.choose(['甲', '乙'])
        self.assertIn(pick.name, ('甲', '乙'))
        self.assertIn('自身强度', pick.reasons[0])

    def test_mlp_json_roundtrip(self):
        policy, _, _, _ = self.build()
        with tempfile.TemporaryDirectory() as directory:
            path = policy.save(Path(directory) / 'latent16_mlp.json')
            payload = json.loads(path.read_text(encoding='utf-8'))
            loaded = LatentPolicy.load(path)
        self.assertEqual(payload['head'], 'mlp')
        self.assertNotIn('query', payload)
        self.assertEqual(loaded.head, 'mlp')
        self.assertTrue(np.allclose(loaded.score(['甲', '乙'], ['丙', '丁']),
                                    policy.score(['甲', '乙'], ['丙', '丁'])))


class FittingTests(unittest.TestCase):
    @staticmethod
    def synthetic_rows(seed=0, count=240, names=None):
        names = names or sorted(lineup.CATALOG)[:10]
        index = {name: row for row, name in enumerate(names)}
        rng = np.random.default_rng(seed)
        z = rng.normal(size=(len(names), 4))
        q = rng.normal(size=4)
        m = rng.normal(size=(4, 4))
        rows = []
        for number in range(count):
            offers = list(rng.choice(names, size=3, replace=False))
            size = int(rng.integers(0, 3))
            team = list(rng.choice([name for name in names if name not in offers],
                                   size=size, replace=False))
            scores = np.array([q @ z[index[name]]
                               + (np.mean([z[index[name]] @ m @ z[index[t]] for t in team])
                                  if team else 0.0) for name in offers])
            rows.append({'episode': number // 6, 'round': number % 6 + 1,
                         'offers': offers, 'team': team,
                         'pick': offers[int(np.argmax(scores))],
                         'picked_index': int(np.argmax(scores))})
        return rows, index

    def test_backprop_matches_finite_difference(self):
        rows, index = self.synthetic_rows()
        data = fit.SplitData(rows, index, team_size=2)
        model = fit.LatentModel(len(index), 4, seed=0)
        errors = fit.gradient_check(model, data, samples=5, weight_decay=0.01)
        self.assertLess(max(errors.values()), 1e-6, errors)
        self.assertEqual(set(errors), {'Z', 'query', 'matrix'})

    def test_model_recovers_known_latent_structure(self):
        rows, index = self.synthetic_rows(seed=3, count=300)
        data = fit.SplitData(rows, index, team_size=2)
        model = fit.LatentModel(len(index), 4, seed=0)
        fit.train(model, data, data, epochs=150, batch_size=32, lr=0.1,
                  weight_decay=0.0, eval_every=150, seed=0, verbose=False)
        metrics = fit.evaluate(model, data)
        self.assertGreater(metrics['accuracy'], 0.95)

    def test_split_keeps_episodes_disjoint(self):
        dataset = fit.build_dataset(episodes=8, rounds=3, seed=5)
        split = fit.split_dataset(dataset, ratio=0.8, seed=1)
        self.assertEqual(len(split['train_episodes']) + len(split['test_episodes']), 8)
        self.assertFalse(set(split['train_episodes']) & set(split['test_episodes']))
        self.assertTrue(all(row['episode'] in split['train_episodes'] for row in split['train']))
        self.assertTrue(all(row['episode'] in split['test_episodes'] for row in split['test']))
        self.assertEqual(len(split['train']) + len(split['test']), len(dataset['decisions']))

    def test_dataset_generation_is_reproducible(self):
        first = fit.build_dataset(episodes=3, rounds=5, seed=11)
        second = fit.build_dataset(episodes=3, rounds=5, seed=11)
        other = fit.build_dataset(episodes=3, rounds=5, seed=12)
        self.assertEqual(first['decisions'], second['decisions'])
        self.assertNotEqual(first['decisions'], other['decisions'])
        # 每局 5 轮，队伍逐个增长，且不会重复选同一张卡
        for episode in range(3):
            rows = [row for row in first['decisions'] if row['episode'] == episode]
            self.assertEqual([row['round'] for row in rows], [1, 2, 3, 4, 5])
            self.assertEqual([len(row['team']) for row in rows], [0, 1, 2, 3, 4])
            self.assertEqual(len({row['pick'] for row in rows}), 5)
            for row in rows:
                self.assertIn(row['pick'], row['offers'])
                self.assertEqual(row['offers'][row['picked_index']], row['pick'])

    def test_exported_policy_matches_trained_model(self):
        rows, index = self.synthetic_rows(seed=7, count=60)
        data = fit.SplitData(rows, index, team_size=2)
        model = fit.LatentModel(len(index), 4, seed=0)
        fit.train(model, data, data, epochs=20, batch_size=16, lr=0.05,
                  weight_decay=0.01, eval_every=20, seed=0, verbose=False)
        names = sorted(index, key=index.get)
        policy = LatentPolicy(names, model.Z, model.query, model.matrix)
        with tempfile.TemporaryDirectory() as directory:
            path = policy.save(Path(directory) / 'latent16.json')
            loaded = LatentPolicy.load(path)
        self.assertEqual(loaded.dim, 4)
        for number, row in enumerate(data.rows[:10]):
            expected = model.scores(data.candidates[number:number + 1],
                                    data.team[number:number + 1],
                                    data.counts[number:number + 1])[0]
            self.assertTrue(np.allclose(policy.score(row['offers'], row['team']),
                                        loaded.score(row['offers'], row['team']), atol=1e-5))
            self.assertTrue(np.allclose(policy.score(row['offers'], row['team']), expected,
                                        atol=1e-5))

    def test_shallow_net_backprop_matches_finite_difference(self):
        rows, index = self.synthetic_rows()
        data = fit.SplitData(rows, index, team_size=2)
        net = fit.ShallowNet(len(index), 4, hidden=8, seed=0)
        errors = fit.gradient_check(net, data, samples=3, weight_decay=0.01)
        self.assertLess(max(errors.values()), 1e-6, errors)
        self.assertEqual(set(errors), {'Z', 'matrix', 'W1', 'b1', 'W2', 'b2'})

    def test_folds_are_balanced_and_disjoint(self):
        folds = fit.split_episodes_into_folds(list(range(7)), 5, seed=1)
        sizes = [len(bucket) for bucket in folds]
        self.assertLessEqual(max(sizes) - min(sizes), 1)
        self.assertEqual(sum(sizes), 7)
        self.assertEqual(len(set().union(*folds)), 7)
        self.assertEqual(set().union(*folds), set(range(7)))

    def test_cross_validation_runs_for_both_heads(self):
        dataset = fit.build_dataset(episodes=8, rounds=3, seed=21)
        names = sorted(lineup.CATALOG)
        index = {name: i for i, name in enumerate(names)}
        results = fit.cross_validate(dataset, names, index, folds=4, rounds=3, epochs=5,
                                     batch_size=16, weight_decay=0.01, seed=0,
                                     train_mlp=True, mlp_hidden=8, mlp_epochs=5,
                                     verbose=False)
        self.assertEqual(len(results['bilinear']), 4)
        self.assertEqual(len(results['mlp']), 4)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in results['bilinear']))

    def test_exported_mlp_policy_matches_net(self):
        rows, index = self.synthetic_rows(seed=9, count=60)
        data = fit.SplitData(rows, index, team_size=2)
        net = fit.ShallowNet(len(index), 4, hidden=8, seed=0)
        fit.train(net, data, data, epochs=20, batch_size=16, lr=0.05,
                  weight_decay=0.0, eval_every=20, seed=0, verbose=False)
        names = sorted(index, key=index.get)
        policy = LatentPolicy(names, net.Z, None, net.matrix, head='mlp', head_params={
            'W1': net.W1, 'b1': net.b1, 'W2': net.W2, 'b2': net.b2})
        expected = net.scores(data.candidates[:8], data.team[:8], data.counts[:8])
        actual = np.array([policy.score(row['offers'], row['team']) for row in data.rows[:8]])
        self.assertTrue(np.allclose(actual, expected, atol=1e-6))


class EpisodeTests(unittest.TestCase):
    @staticmethod
    def episode_rows(seed=0, episodes=10, rounds=5):
        """按"真值隐向量"生成整局 5 轮，后几轮依赖前几轮的选择。"""
        names = sorted(lineup.CATALOG)[:10]
        index = {name: row for row, name in enumerate(names)}
        rng = np.random.default_rng(seed)
        z = rng.normal(size=(len(names), 4))
        q = rng.normal(size=4)
        m = rng.normal(size=(4, 4))
        rows = []
        for episode in range(episodes):
            team, used = [], set()
            for round_number in range(1, rounds + 1):
                offers = []
                while len(offers) < 3:
                    name = names[int(rng.integers(0, len(names)))]
                    if name not in used and name not in offers:
                        offers.append(name)
                scores = [
                    q @ z[index[name]]
                    + (np.mean([z[index[name]] @ m @ z[index[member]] for member in team])
                       if team else 0.0)
                    for name in offers
                ]
                best = int(np.argmax(scores))
                rows.append({'episode': episode, 'round': round_number,
                             'offers': offers, 'team': list(team),
                             'pick': offers[best], 'picked_index': best})
                team.append(offers[best])
                used.add(offers[best])
        return rows, index

    def test_episode_data_keeps_round_order(self):
        rows, index = self.episode_rows(episodes=4)
        data = fit.EpisodeData(rows, index, 5)
        self.assertEqual(data.size, 4)
        self.assertEqual(data.offers.shape, (4, 5, 3))
        self.assertEqual(data.teams.shape, (4, 5, 4))
        # 第 r 轮正好带 r-1 张前几轮选出的卡
        self.assertEqual(data.counts[0].tolist(), [0, 1, 2, 3, 4])
        for step, row in enumerate(data.rows[0]):
            self.assertEqual(row['team'], [item['pick'] for item in data.rows[0][:step]])
        candidates, team, counts, labels = data.batch([0, 1])
        self.assertEqual(candidates.shape, (10, 3))
        self.assertEqual(team.shape, (10, 4))
        self.assertEqual(len(counts), 10)

    def test_train_episodes_and_rollout(self):
        rows, index = self.episode_rows(seed=3, episodes=16)
        data = fit.EpisodeData(rows, index, 5)
        model = fit.LatentModel(len(index), 4, seed=0)
        fit.train_episodes(model, data, data, epochs=120, batch_size=4, lr=0.1,
                           weight_decay=0.0, eval_every=120, seed=0, verbose=False)
        metrics = fit.evaluate_episodes(model, data)
        self.assertGreater(metrics['accuracy'], 0.95)
        policy = LatentPolicy(sorted(index, key=index.get), model.Z, model.query,
                              model.matrix)
        rollout = fit.rollout_metrics(policy, data)
        self.assertGreater(rollout['accuracy'], 0.9)
        self.assertEqual(rollout['episodes'], data.size)


class FieldPolicyTests(unittest.TestCase):
    NAMES = ['甲', '乙', '丙', '丁', '戊']
    VECTORS = [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0], [2.0, 0.0], [0.0, 2.0]]
    QUERY = [0.5, 0.5]
    MATRIX = [[0.0, 1.0], [1.0, 0.0]]

    def policy(self):
        return FieldPolicy(self.NAMES, self.VECTORS, self.QUERY, self.MATRIX)

    def test_set_score_is_unary_plus_pairs(self):
        policy = self.policy()
        # 单卡：甲 .5、乙 .5、丙 1.0；两两：甲-乙 1、甲-丙 1、乙-丙 1
        self.assertAlmostEqual(policy.score_set(['甲', '乙', '丙']), 5.0)
        self.assertAlmostEqual(policy.score_set(['丙', '甲', '乙']), 5.0)  # 与顺序无关
        self.assertAlmostEqual(policy.score_set(['甲', '乙']), 0.5 + 0.5 + 1.0)

    def test_choose_picks_the_best_trio(self):
        policy = self.policy()
        team = list(self.NAMES)
        pick = policy.choose(team)
        self.assertIsInstance(pick, FieldPick)
        self.assertEqual(len(pick.names), 3)
        self.assertTrue(set(pick.names) <= set(team))
        best = max(policy.score_set([team[i] for i in combo])
                   for combo in combinations(range(5), 3))
        self.assertAlmostEqual(pick.score, best)
        self.assertEqual(policy.choose(team), policy.choose(team))

    def test_field_json_roundtrip(self):
        policy = self.policy()
        with tempfile.TemporaryDirectory() as directory:
            path = policy.save(Path(directory) / 'field16.json')
            payload = json.loads(path.read_text(encoding='utf-8'))
            loaded = FieldPolicy.load(path)
        self.assertEqual(payload['format'], FIELD_FORMAT)
        self.assertAlmostEqual(loaded.score_set(['甲', '乙', '丙']), 5.0)
        self.assertEqual(loaded.names, policy.names)


class FieldFitTests(unittest.TestCase):
    @staticmethod
    def field_records(seed=0, count=60):
        names = sorted(lineup.CATALOG)[:10]
        index = {name: row for row, name in enumerate(names)}
        rng = np.random.default_rng(seed)
        records = []
        for number in range(count):
            team = [str(name) for name in rng.choice(names, size=5, replace=False)]
            field = sorted(str(name) for name in rng.choice(team, size=3, replace=False))
            records.append({'episode': number, 'team': team, 'field': field})
        return records, index

    def test_field_backprop_matches_finite_difference(self):
        records, index = self.field_records()
        data = field_fit.FieldData(records, index)
        model = field_fit.FieldModel(len(index), 4, seed=0)
        errors = field_fit.gradient_check_field(model, data, samples=3,
                                                weight_decay=0.01)
        self.assertLess(max(errors.values()), 1e-6, errors)
        self.assertEqual(set(errors), {'U', 'query', 'matrix'})

    def test_field_model_recovers_synthetic_teacher(self):
        names = sorted(lineup.CATALOG)[:8]
        index = {name: row for row, name in enumerate(names)}
        rng = np.random.default_rng(5)
        truth = field_fit.FieldModel(len(names), 4, seed=1)
        combos = list(combinations(range(5), 3))
        records = []
        for number in range(240):
            team = sorted(int(value) for value in rng.choice(len(names), size=5,
                                                             replace=False))
            sets = np.array(team)[np.array(combos)]
            scores = truth.scores(sets[None, ...])[0]
            chosen = combos[int(np.argmax(scores))]
            records.append({'episode': number,
                            'team': [names[team[i]] for i in range(5)],
                            'field': sorted(names[team[i]] for i in chosen)})
        data = field_fit.FieldData(records, index)
        model = field_fit.FieldModel(len(names), 4, seed=0)
        field_fit.train_field(model, data, data, epochs=250, batch_size=32, lr=0.05,
                              weight_decay=0.0, eval_every=250, seed=0, verbose=False)
        self.assertGreater(field_fit.evaluate_field(model, data)['accuracy'], 0.9)


if __name__ == '__main__':
    unittest.main()
