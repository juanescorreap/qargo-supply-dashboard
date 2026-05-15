"""
Supply Consumption Analysis - LOCAL PROCESSING SCRIPT
=====================================================

INSTRUCCIONES DE USO:

1. PREPARAR TUS ARCHIVOS:
   - Coloca TODOS los CSVs de ventas en una carpeta (ej: C:/ventas_cafeterias/)
   - Formato de archivos: {STORE_CODE}_YYYYMMDD-YYYYMMDD.csv
   - Ejemplo: OC_20251201-20251231.csv, MB_20251201-20251231.csv, etc.

2. COLOCAR ARCHIVOS DE CONFIGURACIÓN en la MISMA carpeta que este script:
   - ingredient_classification_FINAL.csv (ya lo tienes)
   - recipes_flat.csv (ya lo tienes)
   - packaging_template_COMPLETE.csv (para vasos, tapas, mangas, pajitas, servilletas)

3. INSTALAR DEPENDENCIAS (si no las tienes):
   pip install pandas numpy

4. EJECUTAR:
   python process_sales_LOCAL.py

5. OUTPUTS:
   - consumption_by_store_week.csv - Consumo detallado por tienda-semana-categoría-subcategoría
   - consumption_summary.csv - Promedios finales por tienda
   - bev_food_stats.csv - Distribución Beverages vs Food (para dashboard)
   - analysis_report.txt - Reporte con estadísticas

================================================================================
"""

import pandas as pd
import numpy as np
import unicodedata
from pathlib import Path
from datetime import datetime
import os
import glob

def _normalize(s):
    """Elimina acentos y pasa a minúsculas para matching robusto."""
    return unicodedata.normalize('NFD', str(s)).encode('ascii', 'ignore').decode('utf-8').lower().strip()

# ============================================================================
# CONFIGURACIÓN
# ============================================================================

SALES_FOLDER = r"/home/juan_esteban_correa/analisis_supplies/ventas"

RECIPES_FILE = "recipes_flat.csv"
CLASSIFICATION_FILE = "ingredient_classification_FINAL.csv"
PACKAGING_FILE = "packaging_template_COMPLETE.csv"

WASTE_PERCENTAGE = 0.07
MIN_RELIABLE_WEEKS = 8

# Tiendas excluidas completamente del análisis
EXCLUDED_STORES = ['183', 'Nashua', 'Lab-01']

CATEGORY_DISPLAY = {
    ('Coffee', 'g'):           'Café Espresso / Molido (kg)',
    ('Coffee', 'ml'):          'Cold Brew / Café en Litros (L)',
    ('Dairy', 'ml'):           'Lácteos Líquidos (L)',
    ('Dairy', 'g'):            'Lácteos Sólidos (kg)',
    ('Specialty Milk', 'ml'):  'Specialty Milk - Plant-based (L)',
    ('Syrups', 'pump'):        'Syrups (pumps)',
    ('Syrups', 'g'):           'Syrups (kg)',
    ('Plastic goods', 'cup'):  'Vasos (unidades)',
    ('Plastic goods', 'lid'):  'Tapas (unidades)',
    ('Plastic goods', 'sleeve'): 'Mangas (unidades)',
    ('Plastic goods', 'straw'):  'Pajitas (unidades)',
    ('Plastic goods', 'napkin'): 'Servilletas (unidades)',
    ('Paper goods', 'unit'):   'Paper goods (unidades)',
}

def to_order_unit(value, unit):
    """Convierte a unidad de compra: g→kg, ml→L."""
    if unit == 'g':
        return value / 1000, 'kg'
    if unit == 'ml':
        return value / 1000, 'L'
    return value, unit

# ============================================================================
# CARGA DE DATOS
# ============================================================================

def load_recipes():
    print("Cargando recipe book...")
    df = pd.read_csv(RECIPES_FILE)
    print(f"  ✓ {len(df)} líneas de recetas cargadas ({df['product_name'].nunique()} productos)")
    return df

def load_classification():
    print("Cargando clasificación de ingredientes...")
    df = pd.read_csv(CLASSIFICATION_FILE)
    core_categories = ['Dairy', 'Coffee', 'Plastic goods', 'Paper goods', 'Specialty Milk', 'Syrups']
    df_core = df[df['category'].isin(core_categories)].copy()
    print(f"  ✓ {len(df_core)} ingredientes en categorías core")
    return df_core

def load_packaging():
    if not os.path.exists(PACKAGING_FILE):
        print(f"⚠️  Packaging no encontrado ({PACKAGING_FILE}) — default: 1 vaso + 1 tapa por bebida")
        return None
    print("Cargando tabla de packaging...")
    df = pd.read_csv(PACKAGING_FILE)
    print(f"  ✓ {len(df)} líneas de packaging cargadas ({df['product_name'].nunique()} productos)")
    return df

# ============================================================================
# PARSING DE VENTAS
# ============================================================================

def extract_product_size(item_name):
    """
    Extrae producto y tamaño de un nombre de ítem del POS.
    "16 Oz Iced Latte" → ("Iced Latte", "16oz")
    """
    parts = str(item_name).split()
    for i, part in enumerate(parts):
        if part.isdigit() and i + 1 < len(parts) and parts[i + 1].lower() == 'oz':
            return ' '.join(parts[i + 2:]), f"{parts[i]}oz"
    return item_name, '-'

def _is_excluded_store(store_name):
    s = str(store_name).lower()
    return any(exc.lower() in s for exc in EXCLUDED_STORES)

def parse_sales_csv(filepath):
    """
    Parsea un CSV mensual del POS.
    Retorna (df_beverages_summary, bev_count, food_count).
    bev_count / food_count: líneas POS sin modificadores, para el ratio Beverages vs Food.
    """
    df = pd.read_csv(filepath)
    df['store'] = df['Location']
    df['date'] = pd.to_datetime(df['Closed Date/Time'], format='%m/%d/%Y %I:%M %p', errors='coerce')
    df['week'] = df['date'].dt.to_period('W').astype(str)

    df_non_mod = df[df['Is Modifier'] == False].copy()
    df_beverages = df_non_mod[df_non_mod['Revenue Center'] == 'Beverages'].copy()
    df_food = df_non_mod[df_non_mod['Revenue Center'] != 'Beverages'].copy()

    bev_count = len(df_beverages)
    food_count = len(df_food)

    df_beverages[['product', 'size']] = df_beverages['Item Name'].apply(
        lambda x: pd.Series(extract_product_size(x))
    )

    df_summary = df_beverages.groupby(
        ['store', 'week', 'product', 'size']
    ).size().reset_index(name='quantity')

    return df_summary, bev_count, food_count

# ============================================================================
# MATCHING RECETAS
# ============================================================================

def _find_match(product_clean, lookup_df, name_col):
    """
    Encuentra el producto más parecido a product_clean en lookup_df[name_col].
    Estrategia: 1) exacto, 2) nombre de receta contenido en POS (más largo gana),
    3) máximo solapamiento de palabras.
    """
    unique_names = lookup_df[name_col].dropna().unique()
    norm_map = {_normalize(n): n for n in unique_names}
    norm_product = _normalize(product_clean)

    if norm_product in norm_map:
        original = norm_map[norm_product]
        return lookup_df[lookup_df[name_col] == original], 'exact'

    best_contained = None
    best_contained_len = 0
    for norm_recipe, original_recipe in norm_map.items():
        if norm_recipe in norm_product and len(norm_recipe) > best_contained_len:
            best_contained = original_recipe
            best_contained_len = len(norm_recipe)
    if best_contained:
        return lookup_df[lookup_df[name_col] == best_contained], 'exact'

    product_words = {w for w in norm_product.split() if len(w) > 3}
    if not product_words:
        return None, None

    best_match = None
    best_score = 0
    for norm_recipe, original_recipe in norm_map.items():
        recipe_words = {w for w in norm_recipe.split() if len(w) > 3}
        score = len(product_words & recipe_words)
        if score > best_score:
            best_score = score
            best_match = original_recipe

    if best_match and best_score > 0:
        return lookup_df[lookup_df[name_col] == best_match], 'word'

    return None, None

def match_product_to_recipe(product_name, size, recipes_df):
    product_clean = product_name.strip().lower()
    matches, match_type = _find_match(product_clean, recipes_df, 'product_name')

    if matches is None or len(matches) == 0:
        return None, None

    size_map = {
        '4oz': 'size_1', '8oz': 'size_1', '12oz': 'size_1',
        '16oz': 'size_1', '20oz': 'size_2', '24oz': 'size_3', '-': 'size_1',
    }
    size_col = size_map.get(size, 'size_1')
    matches_size = matches[matches['size'] == size_col].copy()

    if len(matches_size) == 0:
        matches_size = matches.copy()

    return matches_size, match_type

# ============================================================================
# CÁLCULO DE CONSUMO
# ============================================================================

def calculate_consumption(sales_df, recipes_df, classification_df):
    """
    Calcula consumo de ingredientes core por tienda-semana.
    Columna 'subcategory': nombre del ingrediente para Dairy, Specialty Milk y Syrups.
    """
    print("\nCalculando consumo de ingredientes...")

    results = []
    total_products = len(sales_df)
    stats = {'exact': 0, 'word': 0, 'no_match': 0, 'no_core_ingredients': 0}
    unmatched_products = set()
    word_matched_products = []

    for idx, (_, sale) in enumerate(sales_df.iterrows()):
        if (idx + 1) % 500 == 0:
            print(f"  Procesando {idx + 1}/{total_products}...")

        recipe_matches, match_type = match_product_to_recipe(
            sale['product'], sale['size'], recipes_df
        )

        if recipe_matches is None:
            stats['no_match'] += 1
            unmatched_products.add(sale['product'])
            continue

        if match_type == 'word':
            word_matched_products.append((sale['product'], recipe_matches['product_name'].iloc[0]))

        found_core = False

        for _, ingredient in recipe_matches.iterrows():
            ing_class = classification_df[
                classification_df['ingredient'] == ingredient['ingredient']
            ]
            if len(ing_class) == 0:
                continue

            found_core = True
            category = ing_class.iloc[0]['category']
            unit = ing_class.iloc[0]['unit_base']

            # subcategory: nombre del ingrediente cuando aplica
            subcategory = ingredient['ingredient'] if category in ('Dairy', 'Specialty Milk', 'Syrups') else ''

            qty_str = str(ingredient['quantity'])
            try:
                qty_raw = qty_str.split()[0] if qty_str.split() else '0'
                qty_num = float(''.join(c for c in qty_raw if c.isdigit() or c == '.'))

                if ingredient['unit'] == 'Oz' and unit == 'ml':
                    qty_num = qty_num * 29.5735
                elif ingredient['unit'] in ('Shot', 'Shots'):
                    qty_num = qty_num * (42.5 if 'Lungo' in qty_str else 28)
                    unit = 'g'
                elif ingredient['unit'] in ('Pump', 'Pumps') and unit != 'pump':
                    # Solo convierte pump→g si la clasificación no es pump (ej: Syrups usan 'pump')
                    qty_num = qty_num * 7
                    unit = 'g'

                results.append({
                    'store':        sale['store'],
                    'week':         sale['week'],
                    'category':     category,
                    'subcategory':  subcategory,
                    'unit':         unit,
                    'consumption':  qty_num * sale['quantity'],
                })

            except Exception:
                continue

        if found_core:
            stats[match_type] += 1
        else:
            stats['no_core_ingredients'] += 1

    total_matched = stats['exact'] + stats['word']
    print(f"\n  Match exacto:                {stats['exact']:>5}")
    print(f"  Match por palabra:           {stats['word']:>5}  ← revisar si son correctos")
    print(f"  Sin match (sin receta):      {stats['no_match']:>5}")
    print(f"  Receta sin ingrediente core: {stats['no_core_ingredients']:>5}  ← posible receta incompleta")
    print(f"  Tasa de match: {total_matched}/{total_products} ({total_matched/total_products*100:.1f}%)")

    if word_matched_products:
        unique_word = list(dict.fromkeys(word_matched_products))[:10]
        print(f"\n  Muestra de matches por palabra (primeros 10):")
        for sold, recipe in unique_word:
            print(f"    '{sold}' → '{recipe}'")

    if unmatched_products:
        print(f"\n  Productos sin receta (primeros 10): {list(unmatched_products)[:10]}")

    df_results = pd.DataFrame(results)
    if len(df_results) == 0:
        return pd.DataFrame(columns=['store', 'week', 'category', 'subcategory', 'unit', 'consumption'])

    return df_results.groupby(
        ['store', 'week', 'category', 'subcategory', 'unit']
    )['consumption'].sum().reset_index()


def calculate_packaging_consumption(sales_df, packaging_df):
    """
    Calcula consumo de packaging por tienda-semana.
    Cups incluyen subcategory con tamaño: cup_12oz, cup_16oz, cup_20oz, cup_24oz.
    """
    print("\nCalculando packaging (vasos, tapas, mangas, etc.)...")

    # Mapa para subcategoría de vaso (tamaño real del vaso)
    cup_size_map = {
        '4oz': 'cup_12oz', '8oz': 'cup_12oz', '12oz': 'cup_12oz',
        '16oz': 'cup_16oz', '20oz': 'cup_20oz', '24oz': 'cup_24oz', '-': 'cup_16oz',
    }
    # Mapa de tamaño para buscar en template (solo hay 16oz/20oz/24oz en el template)
    template_size_map = {
        '4oz': '16oz', '8oz': '16oz', '12oz': '16oz',
        '16oz': '16oz', '20oz': '20oz', '24oz': '24oz', '-': '16oz',
    }

    packaging_items = ['cup', 'lid', 'napkin', 'straw', 'sleeve', 'bag']
    results = []
    pkg_matched = 0
    pkg_default = 0

    for _, sale in sales_df.iterrows():
        product_clean = sale['product'].strip().lower()
        cup_subcat = cup_size_map.get(sale['size'], 'cup_16oz')
        template_size = template_size_map.get(sale['size'], '16oz')
        qty = sale['quantity']

        pkg_row = None
        if packaging_df is not None:
            matches, _ = _find_match(product_clean, packaging_df, 'product_name')
            if matches is not None and len(matches) > 0:
                size_match = matches[matches['size'] == template_size]
                pkg_row = size_match.iloc[0] if len(size_match) > 0 else matches.iloc[0]
                pkg_matched += 1

        if pkg_row is not None:
            for item in packaging_items:
                item_qty = pkg_row.get(item, 0)
                if pd.notna(item_qty) and item_qty > 0:
                    subcategory = cup_subcat if item == 'cup' else item
                    results.append({
                        'store': sale['store'], 'week': sale['week'],
                        'category': 'Plastic goods',
                        'subcategory': subcategory,
                        'unit': item,
                        'consumption': item_qty * qty,
                    })
        else:
            pkg_default += 1
            results.append({
                'store': sale['store'], 'week': sale['week'],
                'category': 'Plastic goods', 'subcategory': cup_subcat,
                'unit': 'cup', 'consumption': qty,
            })
            results.append({
                'store': sale['store'], 'week': sale['week'],
                'category': 'Plastic goods', 'subcategory': 'lid',
                'unit': 'lid', 'consumption': qty,
            })

    total = len(sales_df)
    print(f"  Con template: {pkg_matched}/{total} ({pkg_matched/total*100:.1f}%)")
    print(f"  Con default (1 vaso+tapa): {pkg_default}")

    if not results:
        return pd.DataFrame(columns=['store', 'week', 'category', 'subcategory', 'unit', 'consumption'])

    df = pd.DataFrame(results)
    return df.groupby(['store', 'week', 'category', 'subcategory', 'unit'])['consumption'].sum().reset_index()


def apply_waste(df_consumption, waste_pct=0.07):
    """Aplica % de merma a Dairy, Specialty Milk y Coffee."""
    df = df_consumption.copy()
    perishable = df['category'].isin(['Dairy', 'Coffee', 'Specialty Milk'])
    df['waste'] = 0.0
    df.loc[perishable, 'waste'] = df.loc[perishable, 'consumption'] * waste_pct
    df['consumption_with_waste'] = df['consumption'] + df['waste']
    return df


def calculate_averages(df_consumption):
    """Promedio semanal por tienda-categoría-subcategoría-unidad."""
    weeks_per_store = (
        df_consumption.groupby('store')['week']
        .nunique()
        .reset_index(name='weeks_with_data')
    )

    df_avg = (
        df_consumption
        .groupby(['store', 'category', 'subcategory', 'unit'])
        .agg(avg_weekly_consumption=('consumption_with_waste', 'mean'))
        .reset_index()
    )

    return df_avg.merge(weeks_per_store, on='store')

# ============================================================================
# REPORTE
# ============================================================================

def _write_report(df_averages, df_all_sales, sales_files, total_bev, total_food, excluded_found):
    total_trans = total_bev + total_food
    bev_pct  = total_bev  / total_trans * 100 if total_trans > 0 else 0
    food_pct = total_food / total_trans * 100 if total_trans > 0 else 0

    with open('analysis_report.txt', 'w', encoding='utf-8') as f:
        f.write("SUPPLY CONSUMPTION ANALYSIS REPORT\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Fecha de análisis: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Archivos procesados: {len(sales_files)}\n")
        f.write(f"Tiendas: {df_all_sales['store'].nunique()}\n")
        f.write(f"Semanas con datos: {df_all_sales['week'].nunique()}\n\n")

        if excluded_found:
            f.write(f"⚠️  Tiendas excluidas: {', '.join(str(s) for s in excluded_found)}\n\n")

        f.write("DISTRIBUCIÓN BEBIDAS VS COMIDAS\n")
        f.write("-" * 80 + "\n")
        f.write(f"Bebidas:     {bev_pct:5.1f}%  ({total_bev:,} transacciones)\n")
        f.write(f"Comida/Otro: {food_pct:5.1f}%  ({total_food:,} transacciones)\n\n")

        f.write("NOTA SOBRE UNIDADES\n")
        f.write("-" * 80 + "\n")
        f.write("  Café Espresso/Molido      → kg\n")
        f.write("  Cold Brew / Drip Coffee   → L\n")
        f.write("  Lácteos tradicionales     → L o kg\n")
        f.write("  Specialty Milk (plant-based) → L\n")
        f.write("  Syrups                    → pumps\n")
        f.write("  Packaging                 → unidades\n\n")

        f.write("RESUMEN POR TIENDA\n")
        f.write("-" * 80 + "\n\n")

        for store in sorted(df_averages['store'].unique()):
            store_data = df_averages[df_averages['store'] == store]
            weeks = store_data['weeks_with_data'].iloc[0]
            tag = "(confiable)" if weeks >= MIN_RELIABLE_WEEKS else f"⚠️  SOLO {weeks} SEMANA(S) - DATOS INSUFICIENTES"

            f.write(f"Tienda: {store}\n")
            f.write(f"  Semanas de datos: {weeks} {tag}\n\n")

            sections = [
                ('INGREDIENTES CAFÉ',          ['Coffee']),
                ('LÁCTEOS TRADICIONALES',       ['Dairy']),
                ('SPECIALTY MILK (plant-based)',['Specialty Milk']),
                ('SYRUPS',                      ['Syrups']),
                ('PACKAGING',                   ['Plastic goods', 'Paper goods']),
            ]

            for section_name, categories in sections:
                section_data = store_data[store_data['category'].isin(categories)]
                if len(section_data) == 0:
                    continue
                f.write(f"  {section_name}:\n")
                for _, row in section_data.iterrows():
                    val, unit_out = to_order_unit(row['avg_weekly_consumption'], row['unit'])
                    label = row['subcategory'] if row['subcategory'] else CATEGORY_DISPLAY.get(
                        (row['category'], row['unit']),
                        f"{row['category']} ({unit_out})"
                    )
                    f.write(f"    {label:<42}: {val:>10.2f} {unit_out}/semana\n")
                f.write("\n")

            f.write("\n")

# ============================================================================
# MAIN
# ============================================================================

def main():
    print("=" * 80)
    print("SUPPLY CONSUMPTION ANALYSIS - LOCAL PROCESSING")
    print("=" * 80)

    recipes_df = load_recipes()
    classification_df = load_classification()
    packaging_df = load_packaging()

    print(f"\nBuscando archivos CSV en: {SALES_FOLDER}")
    sales_files = glob.glob(os.path.join(SALES_FOLDER, "*.csv"))
    if not sales_files:
        print(f"❌ No se encontraron archivos en {SALES_FOLDER}")
        return

    print(f"  ✓ {len(sales_files)} archivos encontrados")
    all_sales = []
    total_bev = 0
    total_food = 0

    for filepath in sorted(sales_files):
        filename = os.path.basename(filepath)
        print(f"  Procesando {filename}...")
        try:
            sales_data, bev_count, food_count = parse_sales_csv(filepath)
            all_sales.append(sales_data)
            total_bev += bev_count
            total_food += food_count
            print(f"    ✓ {len(sales_data)} registros de bebidas ({sales_data['store'].nunique()} tiendas)")
        except Exception as e:
            print(f"    ✗ ERROR: {e}")

    df_all_sales = pd.concat(all_sales, ignore_index=True)

    # Excluir tiendas 183 y Nashua
    excluded_mask = df_all_sales['store'].apply(_is_excluded_store)
    excluded_found = df_all_sales[excluded_mask]['store'].unique().tolist()
    df_all_sales = df_all_sales[~excluded_mask]

    if excluded_found:
        print(f"\n⚠️  Tiendas excluidas del análisis: {', '.join(str(s) for s in excluded_found)}")

    print(f"\n✓ Total: {len(df_all_sales)} registros de bebidas")
    print(f"  Tiendas: {df_all_sales['store'].nunique()}")
    print(f"  Semanas: {df_all_sales['week'].nunique()}")
    print(f"  Productos únicos: {df_all_sales['product'].nunique()}")

    df_ingredients = calculate_consumption(df_all_sales, recipes_df, classification_df)
    df_packaging = calculate_packaging_consumption(df_all_sales, packaging_df)

    # Unir sin duplicar Plastic goods de recetas
    df_ingredients_no_plastic = df_ingredients[df_ingredients['category'] != 'Plastic goods'].copy()
    df_consumption = pd.concat([df_ingredients_no_plastic, df_packaging], ignore_index=True)

    print("\nAplicando merma/waste (7%) a Dairy, Specialty Milk y Coffee...")
    df_consumption = apply_waste(df_consumption, WASTE_PERCENTAGE)

    print("Calculando promedios semanales...")
    df_averages = calculate_averages(df_consumption)

    # Guardar Beverages vs Food
    total_trans = total_bev + total_food
    bev_pct  = total_bev  / total_trans * 100 if total_trans > 0 else 0
    food_pct = total_food / total_trans * 100 if total_trans > 0 else 0
    pd.DataFrame([
        {'revenue_center': 'Beverages',    'transactions': total_bev,  'percentage': round(bev_pct, 1)},
        {'revenue_center': 'Food & Other', 'transactions': total_food, 'percentage': round(food_pct, 1)},
    ]).to_csv('bev_food_stats.csv', index=False)

    # Guardar resultados
    df_consumption.to_csv('consumption_by_store_week.csv', index=False)
    df_averages.to_csv('consumption_summary.csv', index=False)
    print("\n✓ consumption_by_store_week.csv")
    print("✓ consumption_summary.csv")
    print("✓ bev_food_stats.csv")

    _write_report(df_averages, df_all_sales, sales_files, total_bev, total_food, excluded_found)
    print("✓ analysis_report.txt")

    # Resumen en pantalla
    print("\n" + "=" * 80)
    print("RESUMEN POR CATEGORÍA (promedio entre tiendas confiables)")
    print("=" * 80)
    reliable = df_averages[df_averages['weeks_with_data'] >= MIN_RELIABLE_WEEKS]
    summary = reliable.groupby(['category', 'unit'])['avg_weekly_consumption'].mean().reset_index()
    for _, row in summary.iterrows():
        val, unit_out = to_order_unit(row['avg_weekly_consumption'], row['unit'])
        display = CATEGORY_DISPLAY.get((row['category'], row['unit']), f"{row['category']} ({unit_out})")
        print(f"  {display:<44}: {val:>10.2f} {unit_out}/semana")

    print(f"\n  Beverages:   {bev_pct:.1f}% ({total_bev:,} transacciones)")
    print(f"  Food & Other: {food_pct:.1f}% ({total_food:,} transacciones)")

    if excluded_found:
        print(f"\n⚠️  Tiendas excluidas del análisis: {', '.join(excluded_found)}")

    unreliable = df_averages[df_averages['weeks_with_data'] < MIN_RELIABLE_WEEKS]['store'].unique()
    if len(unreliable) > 0:
        print(f"\n⚠️  Tiendas excluidas del promedio global (< {MIN_RELIABLE_WEEKS} semanas):")
        for s in sorted(unreliable):
            weeks = df_averages[df_averages['store'] == s]['weeks_with_data'].iloc[0]
            print(f"     {s} ({weeks} semana(s))")


if __name__ == "__main__":
    main()
