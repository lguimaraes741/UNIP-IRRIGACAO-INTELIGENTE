"""Treino temporal e inferência de artefatos JSON, sem pickle ou código executável."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import struct

FEATURES = ['soil_index', 'temperature_c', 'humidity_pct', 'hour_sin', 'hour_cos']
# Pre-project: 30-minute prediction (confirm in the pilot). Changing it invalidates artifacts.
HORIZON_S = 1800


def features(item):
    stamp = datetime.fromisoformat(str(item['collected_at']).replace('Z', '+00:00'))
    if stamp.tzinfo is None or not item['sensor_valid']:
        raise ValueError('Leitura inválida ou horário sem timezone')
    stamp = stamp.astimezone(timezone.utc)
    values = [item['soil_index'], item['temperature_c'], item['humidity_pct']]
    if any(v is None or not math.isfinite(float(v)) for v in values):
        raise ValueError('Atributos ausentes/não finitos')
    if not (0 <= float(values[0]) <= 100 and -40 <= float(values[1]) <= 85 and 0 <= float(values[2]) <= 100):
        raise ValueError('Atributos fora da escala')
    phase = 2 * math.pi * (stamp.hour + stamp.minute / 60 + stamp.second / 3600) / 24
    return [float(v) for v in values] + [math.sin(phase), math.cos(phase)]


def version(body):
    return 'ml-' + hashlib.sha256(json.dumps(body, sort_keys=True, allow_nan=False).encode()).hexdigest()[:24]


def export_tree(model):
    tree = model.tree_
    return dict(left=tree.children_left.tolist(), right=tree.children_right.tolist(),
                feature=tree.feature.tolist(), threshold=tree.threshold.tolist(),
                value=tree.value[:, 0, 0].tolist())


def export_estimator(model, kind):
    if kind == 'ridge':
        scale = model.named_steps['scale']
        ridge = model.named_steps['ridge']
        return dict(kind=kind, mean=scale.mean_.tolist(), scale=scale.scale_.tolist(),
                    coef=ridge.coef_.tolist(), intercept=float(ridge.intercept_))
    if kind == 'random_forest':
        return dict(kind=kind, trees=[export_tree(t) for t in model.estimators_])
    if kind == 'xgboost':
        return export_xgboost(model)
    return dict(kind='gradient_boosting', initial=float(model.init_.constant_[0, 0]),
                learning_rate=float(model.learning_rate),
                trees=[export_tree(t[0]) for t in model.estimators_])


def float32(value):
    return struct.unpack('f', struct.pack('f', value))[0]


def export_xgboost(model):
    """Flatten XGBoost JSON dumps into the same node arrays; splits are `x < threshold` in float32."""
    trees = []
    for dump in model.get_booster().get_dump(dump_format='json'):
        nodes = {}
        pending = [json.loads(dump)]
        while pending:
            node = pending.pop()
            nodes[node['nodeid']] = node
            pending.extend(node.get('children', []))
        size = max(nodes) + 1
        if sorted(nodes) != list(range(size)):
            raise ValueError('Árvore XGBoost com nós ausentes')
        tree = dict(left=[-1] * size, right=[-1] * size, feature=[-2] * size, threshold=[0.0] * size, value=[0.0] * size)
        for nodeid, node in nodes.items():
            if 'leaf' in node:
                tree['value'][nodeid] = float(node['leaf'])
                continue
            if node['missing'] != node['yes']:
                raise ValueError('Ramo de ausentes não suportado')
            tree['left'][nodeid], tree['right'][nodeid] = node['yes'], node['no']
            tree['feature'][nodeid] = int(node['split'].lstrip('f'))
            tree['threshold'][nodeid] = float(node['split_condition'])
        trees.append(tree)
    # Base margin derived from the fitted model so it does not depend on version-specific config text.
    import numpy as np
    probe = np.zeros((1, model.n_features_in_), dtype=np.float32)
    leaves = sum(tree_predict(t, probe[0].tolist(), strict=True) for t in trees)
    return dict(kind='xgboost', base=float(model.predict(probe)[0]) - leaves, trees=trees)


def tree_predict(tree, values, strict=False):
    lengths = [len(tree[k]) for k in ('left', 'right', 'feature', 'threshold', 'value')]
    if len(set(lengths)) != 1 or not 0 < lengths[0] <= 100000:
        raise ValueError('Árvore inválida')
    index = 0
    for _ in range(lengths[0] + 1):
        if not 0 <= index < lengths[0]:
            raise ValueError('Índice de árvore inválido')
        if tree['left'][index] == -1:
            if tree['right'][index] != -1:
                raise ValueError('Folha inválida')
            return float(tree['value'][index])
        feature = tree['feature'][index]
        if not isinstance(feature, int) or not 0 <= feature < len(values):
            raise ValueError('Atributo de árvore inválido')
        if strict:
            go_left = float32(values[feature]) < float32(tree['threshold'][index])
        else:
            go_left = values[feature] <= tree['threshold'][index]
        index = tree['left'][index] if go_left else tree['right'][index]
    raise ValueError('Árvore cíclica')


def estimate(estimator, values):
    kind = estimator['kind']
    if kind == 'ridge':
        if any(len(estimator[k]) != len(FEATURES) for k in ('mean', 'scale', 'coef')):
            raise ValueError('Dimensões incompatíveis')
        if any(s <= 0 for s in estimator['scale']):
            raise ValueError('Escala inválida')
        result = estimator['intercept'] + sum(c * (v - m) / s for c, v, m, s in
                    zip(estimator['coef'], values, estimator['mean'], estimator['scale']))
    else:
        trees = estimator['trees']
        if not 0 < len(trees) <= 1000:
            raise ValueError('Ensemble inválido')
        output = [tree_predict(t, values, strict=kind == 'xgboost') for t in trees]
        if kind == 'random_forest':
            result = sum(output) / len(output)
        elif kind == 'gradient_boosting':
            result = estimator['initial'] + estimator['learning_rate'] * sum(output)
        elif kind == 'xgboost':
            result = estimator['base'] + sum(output)
        else:
            raise ValueError('Modelo não suportado')
    if not math.isfinite(result):
        raise ValueError('Previsão não finita')
    return float(result)


def load_model(path):
    path = Path(path)
    if path.stat().st_size > 20 * 1024 * 1024:
        raise ValueError('Artefato acima do limite')
    artifact = json.loads(path.read_text(encoding='utf-8'), parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    body = artifact['body']
    if artifact['model_version'] != version(body) or body['schema_version'] != 1:
        raise ValueError('Versão/hash de modelo inválido')
    if body['features'] != FEATURES or body['horizon_s'] != HORIZON_S:
        raise ValueError('Atributos/horizonte incompatíveis')
    return artifact


def infer(artifact, item):
    body = artifact['body']
    if body['source'] != item['source'] or body['device_id'] != item['device_id'] or body['unit_id'] != item['unit_id']:
        raise ValueError('Origem/dispositivo/vaso incompatível com o treino')
    if body['calibration_id'] != item['calibration_id']:
        raise ValueError('Calibração incompatível')
    # Artefatos físicos exigem revisão de desempenho antes de serem usados pelo controle.
    if item['source'] == 'physical' and not body.get('deployment_approved', False):
        raise ValueError('Modelo físico ainda não aprovado para implantação')
    result = estimate(body['estimator'], features(item))
    if not 0 <= result <= 100:
        raise ValueError('Previsão fora da escala; não corrigida artificialmente')
    return result


def prepare_dataset(path, source, allow_simulation=False):
    import pandas as pd
    if source == 'simulation' and not allow_simulation:
        raise ValueError('Treino simulado exige --allow-simulation explícito')
    frame = pd.read_csv(path)
    required = ['collected_at', 'device_id', 'unit_id', 'experiment_id', 'source', 'calibration_id',
                'sensor_valid', 'soil_index', 'temperature_c', 'humidity_pct']
    if set(required) - set(frame):
        raise ValueError('CSV sem todos os campos obrigatórios')
    if set(frame.source) != {source}:
        raise ValueError('Dataset mistura origens ou difere da origem solicitada')
    for field in ['device_id', 'unit_id', 'calibration_id']:
        if frame[field].nunique(dropna=False) != 1 or frame[field].isna().any():
            raise ValueError('Treinar separadamente por dispositivo, vaso e calibração')
    if source == 'simulation' and not (frame.device_id.str.startswith('sim-').all() and frame.experiment_id.str.startswith('sim-').all()):
        raise ValueError('Identificação de simulação ausente')
    if source == 'physical' and (frame.device_id.str.startswith('sim-').any() or frame.experiment_id.str.startswith('sim-').any()):
        raise ValueError('Identificação simulada em dados físicos')
    stamps = [datetime.fromisoformat(str(x).replace('Z', '+00:00')) for x in frame.collected_at]
    if any(x.tzinfo is None for x in stamps):
        raise ValueError('Todos os horários devem ter timezone')
    frame['time'] = pd.to_datetime(stamps, utc=True)
    if frame.time.duplicated().any():
        raise ValueError('Horários duplicados; deduplicar antes do treino')
    if not frame.sensor_valid.isin([True, False]).all():
        raise ValueError('sensor_valid deve ser booleano')
    frame = frame[frame.sensor_valid == True].sort_values('time').copy()
    records = []
    # Nunca cruzar experimentos ao construir alvo; o futuro serve apenas ao alvo.
    for _, group in frame.groupby('experiment_id'):
        for row in group.to_dict('records'):
            try:
                row['x'] = features(row)
            except (ValueError, TypeError):
                continue
            wanted = row['time'] + pd.Timedelta(seconds=HORIZON_S)
            index = group.time.searchsorted(wanted)
            if index >= len(group):
                continue
            target = group.iloc[index]
            if target.time > wanted + pd.Timedelta(seconds=60):
                continue
            if pd.isna(target.soil_index) or not 0 <= target.soil_index <= 100:
                continue
            records.append(dict(time=row['time'], target_time=target.time, x=row['x'], y=float(target.soil_index)))
    rows = pd.DataFrame(records).sort_values('time') if records else pd.DataFrame()
    if len(rows) < 60:
        raise ValueError('Menos de 60 pares válidos; quantidade mínima técnica não garante adequação científica')
    return frame, rows.reset_index(drop=True)


def splits(rows, n_splits=3):
    from sklearn.model_selection import TimeSeriesSplit
    for train, validation in TimeSeriesSplit(n_splits=n_splits).split(rows):
        start = rows.iloc[validation[0]].time
        purged = [i for i in train if rows.iloc[i].target_time < start]
        if len(purged) < 10:
            raise ValueError('Janela temporal insuficiente após excluir alvos que cruzam fronteira')
        yield purged, validation.tolist()


def train(path, output, source='physical', allow_simulation=False):
    import numpy as np
    import sklearn
    import xgboost
    from sklearn.ensemble import GradientBoostingRegressor, RandomForestRegressor
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import Pipeline
    from sklearn.preprocessing import StandardScaler
    frame, rows = prepare_dataset(path, source, allow_simulation)
    cutoff = int(len(rows) * .8)
    test = rows.iloc[cutoff:]
    selection = rows.iloc[:cutoff]
    selection = selection[selection.target_time < test.iloc[0].time].reset_index(drop=True)
    if len(selection) < 40 or len(test) < 10:
        raise ValueError('Dados insuficientes para seleção e teste temporal separados')
    factories = {
        'ridge': lambda: Pipeline([('scale', StandardScaler()), ('ridge', Ridge(alpha=1.0))]),
        'random_forest': lambda: RandomForestRegressor(n_estimators=30, max_depth=5, random_state=17, n_jobs=1),
        'gradient_boosting': lambda: GradientBoostingRegressor(n_estimators=30, max_depth=2, random_state=17),
        # Requested by the pre-project; deterministic single-thread fit for reproducibility.
        'xgboost': lambda: xgboost.XGBRegressor(n_estimators=30, max_depth=2, learning_rate=0.1,
                                                random_state=17, n_jobs=1, tree_method='exact'),
    }
    X = np.array(selection.x.tolist())
    y = selection.y.to_numpy()
    metrics = {}
    def score(predictions, truth):
        error = np.asarray(predictions) - np.asarray(truth)
        return dict(mae=float(np.mean(np.abs(error))), rmse=float(np.sqrt(np.mean(error ** 2))))
    folds = list(splits(selection))
    for kind, factory in factories.items():
        errors = []
        for tr, va in folds:
            model = factory().fit(X[tr], y[tr])
            errors.append(score(model.predict(X[va]), y[va])['mae'])
        metrics[kind] = {'validation_mae': float(np.mean(errors))}
    winner = min(metrics, key=lambda key: metrics[key]['validation_mae'])
    model = factories[winner]().fit(X, y)
    Xt = np.array(test.x.tolist())
    final = score(model.predict(Xt), test.y.to_numpy())
    baseline = score(Xt[:, 0], test.y.to_numpy())
    first = frame.iloc[0]
    body = dict(schema_version=1, source=source, device_id=str(first.device_id), unit_id=str(first.unit_id),
                calibration_id=str(first.calibration_id), horizon_s=HORIZON_S, features=FEATURES,
                created_at=datetime.now(timezone.utc).isoformat(), sklearn_version=sklearn.__version__, xgboost_version=xgboost.__version__,
                target_semantics='umidade observada sob a política coletada; não cenário sem irrigação',
                deployment_approved=False, estimator=export_estimator(model, winner),
                validation=metrics, test=final, persistence_test=baseline,
                training_last_target=selection.target_time.max().isoformat(),
                test_first_input=test.iloc[0].time.isoformat(), rows_training=len(selection), rows_test=len(test))
    artifact = dict(model_version=version(body), body=body)
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(artifact, indent=2, allow_nan=False), encoding='utf-8')
    print(json.dumps({'model_version': artifact['model_version'], 'source': source,
                      'winner': winner, 'test': final, 'persistence_test': baseline,
                      'deployment_approved': False}, indent=2))
    return artifact


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('input_csv')
    parser.add_argument('output_json')
    parser.add_argument('--source', choices=['physical', 'simulation'], default='physical')
    parser.add_argument('--allow-simulation', action='store_true')
    args = parser.parse_args()
    train(args.input_csv, args.output_json, args.source, args.allow_simulation)
