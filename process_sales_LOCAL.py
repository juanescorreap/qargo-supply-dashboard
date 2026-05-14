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
   - consumption_by_store_week.csv - Consumo detallado por tienda-semana-categoría
   - consumption_summary.csv - Promedios finales por tienda
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
# CONFIGURACIÓN - EDITAR ESTAS RUTAS
# ============================================================================

SALES_FOLDER = r"/home/juan_esteban_correa/analisis_supplies/ventas"

RECIPES_FILE = "recipes_flat.csv"
CLASSIFICATION_FILE = "ingredient_classification_FINAL.csv"
PACKAGING_FILE = "packaging_template_COMPLETE.csv"

# Merma/Waste como porcentaje (aplica a Dairy y Coffee)
WASTE_PERCENTAGE = 0.07  # 7%

# Tiendas con menos semanas de dato no son confiables para ordenar
MIN_RELIABLE_WEEKS = 8

# Nombres legibles para categoría+unidad (para el reporte y compras)
CATEGORY_DISPLAY = {
    ('Coffee', 'g'):  'Café Espresso / Molido (kg)',
    ('Coffee', 'ml'): 'Cold Brew / Café en Litros (L)',
    ('Dairy', 'ml'):  'Lácteos Líquidos (L)',
    ('Dairy', 'g'):   'Lácteos Sólidos (kg)',
    ('Plastic goods', 'cup'): 'Vasos (unidades)',
    ('Plastic goods', 'lid'): 'Tapas (unidades)',
    ('Plastic goods', 'sleeve'): 'Mangas (unidades)',
    ('Plastic goods', 'straw'): 'Pajitas (unidades)',
    ('Plastic goods', 'napkin'): 'Servilletas (unidades)',
    ('Paper goods', 'unit'): 'Paper goods (unidades)',
}

# Conversión a unidades de compra: g→kg, ml→L
def to_order_unit(value, unit):
    """Convierte a unidad de compra y retorna (valor_convertido, unidad_nueva)"""
    if unit == 'g':
        return value / 1000, 'kg'
    if unit == 'ml':
        return value / 1000, 'L'
    return value, unit

# ============================================================================
# FUNCIONES AUXILIARES
# ============================================================================

def load_recipes():
    print("Cargando recipe book...")
    df = pd.read_csv(RECIPES_FILE)
    print(f"  ✓ {len(df)} líneas de recetas cargadas ({df['product_name'].nunique()} productos)")
    return df

def load_classification():
    print("Cargando clasificación de ingredientes...")
    df = pd.read_csv(CLASSIFICATION_FILE)
    core_categories = ['Dairy', 'Coffee', 'Plastic goods', 'Paper goods']
    df_core = df[df['category'].isin(core_categories)].copy()
    print(f"  ✓ {len(df_core)} ingredientes en categorías core")
    return df_core

def load_packaging():
    if not os.path.exists(PACKAGING_FILE):
        print(f"⚠️  Archivo de packaging no encontrado ({PACKAGING_FILE}) - se usará default (1 vaso + 1 tapa por bebida)")
        return None
    print("Cargando tabla de packaging...")
    df = pd.read_csv(PACKAGING_FILE)
    print(f"  ✓ {len(df)} líneas de packaging cargadas ({df['product_name'].nunique()} productos)")
    return df

def extract_product_size(item_name):
    """
    Extrae producto y tamaño de un nombre de ítem del POS.
    Ejemplos: "16 Oz Iced Latte" → ("Iced Latte", "16oz")
              "4 Oz Espresso Con Panna" → ("Espresso Con Panna", "4oz")
    """
    parts = str(item_name).split()
    for i, part in enumerate(parts):
        if part.isdigit() and i + 1 < len(parts) and parts[i + 1].lower() == 'oz':
            size = f"{parts[i]}oz"
            product = ' '.join(parts[i + 2:])
            return product, size
    return item_name, '-'

def parse_sales_csv(filepath):
    """
    Parsea un CSV mensual de ventas del POS.
    Solo procesa ítems de Revenue Center == 'Beverages' (no food, retail ni loyalty).
    Retorna DataFrame con [store, date, week, product, size, quantity].
    """
    df = pd.read_csv(filepath)

    df['store'] = df['Location']
    df['date'] = pd.to_datetime(df['Closed Date/Time'], format='%m/%d/%Y %I:%M %p', errors='coerce')
    df['week'] = df['date'].dt.to_period('W').astype(str)

    # Solo bebidas base (no modificadores, no comida, no retail)
    # La categoría Beverages es la que tiene consumo de café y lácteos
    df_beverages = df[
        (df['Is Modifier'] == False) &
        (df['Revenue Center'] == 'Beverages')
    ].copy()

    df_beverages[['product', 'size']] = df_beverages['Item Name'].apply(
        lambda x: pd.Series(extract_product_size(x))
    )

    df_summary = df_beverages.groupby(
        ['store', 'week', 'product', 'size']
    ).size().reset_index(name='quantity')

    return df_summary

def _find_match(product_clean, lookup_df, name_col):
    """
    Encuentra el ÚNICO producto más parecido a product_clean en lookup_df[name_col].
    Estrategia (en orden):
      1. Match exacto (con normalización de acentos).
      2. Nombre de receta contenido en el nombre del POS → el más largo gana.
         Ej: "Drip Coffee Hot" contiene "Drip Coffee".
      3. Máximo solapamiento de palabras → el producto con más palabras en común.
    Retorna (filas del producto ganador, tipo_match) o (None, None).
    """
    # Pre-construir tabla de nombres normalizados únicos
    unique_names = lookup_df[name_col].dropna().unique()
    norm_map = {_normalize(n): n for n in unique_names}

    norm_product = _normalize(product_clean)

    # 1. Exact match
    if norm_product in norm_map:
        original = norm_map[norm_product]
        return lookup_df[lookup_df[name_col] == original], 'exact'

    # 2. Nombre de receta contenido en el producto del POS
    #    "Drip Coffee" ⊂ "Drip Coffee Hot"  →  correcto
    #    "Chai Tea Latte" ⊂ "Chai Tea Latte Hot"  →  correcto
    best_contained = None
    best_contained_len = 0
    for norm_recipe, original_recipe in norm_map.items():
        if norm_recipe in norm_product and len(norm_recipe) > best_contained_len:
            best_contained = original_recipe
            best_contained_len = len(norm_recipe)
    if best_contained:
        return lookup_df[lookup_df[name_col] == best_contained], 'exact'

    # 3. Máximo solapamiento de palabras (solo palabras > 3 chars)
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
    """
    Encuentra la receta para un producto vendido, filtrada por tamaño.
    Retorna (DataFrame con ingredientes, tipo_match) o (None, None).
    """
    product_clean = product_name.strip().lower()
    matches, match_type = _find_match(product_clean, recipes_df, 'product_name')

    if matches is None or len(matches) == 0:
        return None, None

    # Mapeo de tamaño del POS al tamaño en recetas
    size_map = {
        '4oz':  'size_1',
        '8oz':  'size_1',
        '12oz': 'size_1',
        '16oz': 'size_1',
        '20oz': 'size_2',
        '24oz': 'size_3',
        '-':    'size_1',
    }
    size_col = size_map.get(size, 'size_1')
    matches_size = matches[matches['size'] == size_col].copy()

    if len(matches_size) == 0:
        matches_size = matches.copy()

    return matches_size, match_type

def calculate_consumption(sales_df, recipes_df, classification_df):
    """
    Calcula consumo de ingredientes core (Coffee, Dairy, Paper goods) por tienda-semana.
    Retorna DataFrame [store, week, category, unit, consumption] y estadísticas de matching.
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

            qty_str = str(ingredient['quantity'])
            try:
                qty_raw = qty_str.split()[0] if qty_str.split() else '0'
                qty_num = float(''.join(c for c in qty_raw if c.isdigit() or c == '.'))

                if ingredient['unit'] == 'Oz' and unit == 'ml':
                    qty_num = qty_num * 29.5735
                elif ingredient['unit'] in ('Shot', 'Shots'):
                    if 'Lungo' in qty_str:
                        qty_num = qty_num * 42.5
                    else:
                        qty_num = qty_num * 28
                    unit = 'g'
                elif ingredient['unit'] in ('Pump', 'Pumps'):
                    qty_num = qty_num * 7
                    unit = 'g'

                total_consumption = qty_num * sale['quantity']

                results.append({
                    'store': sale['store'],
                    'week': sale['week'],
                    'category': category,
                    'unit': unit,
                    'consumption': total_consumption,
                })

            except Exception:
                continue

        if found_core:
            stats[match_type] += 1
        else:
            stats['no_core_ingredients'] += 1

    total_matched = stats['exact'] + stats['word']
    print(f"\n  Match exacto:           {stats['exact']:>5}")
    print(f"  Match por palabra:      {stats['word']:>5}  ← revisar si son correctos")
    print(f"  Sin match (sin receta): {stats['no_match']:>5}")
    print(f"  Receta sin ingrediente core: {stats['no_core_ingredients']:>5}  ← posible receta incompleta")
    print(f"  Tasa de match con consumo: {total_matched}/{total_products} ({total_matched/total_products*100:.1f}%)")

    if word_matched_products:
        unique_word = list(dict.fromkeys(word_matched_products))[:10]
        print(f"\n  Muestra de matches por palabra (primeros 10):")
        for sold, recipe in unique_word:
            print(f"    '{sold}' → '{recipe}'")

    if unmatched_products:
        print(f"\n  Productos sin receta (primeros 10): {list(unmatched_products)[:10]}")

    df_results = pd.DataFrame(results)
    if len(df_results) == 0:
        return pd.DataFrame(columns=['store', 'week', 'category', 'unit', 'consumption'])

    df_summary = df_results.groupby(
        ['store', 'week', 'category', 'unit']
    )['consumption'].sum().reset_index()

    return df_summary

def calculate_packaging_consumption(sales_df, packaging_df):
    """
    Calcula consumo de packaging (vasos, tapas, mangas, pajitas, servilletas)
    usando la packaging template. Si no hay match, asume 1 vaso + 1 tapa por bebida.

    Retorna DataFrame [store, week, category='Plastic goods', unit=item_name, consumption].
    """
    print("\nCalculando packaging (vasos, tapas, mangas, etc.)...")

    size_map = {
        '4oz': '16oz', '8oz': '16oz', '12oz': '16oz',
        '16oz': '16oz', '20oz': '20oz', '24oz': '24oz', '-': '16oz'
    }
    packaging_items = ['cup', 'lid', 'napkin', 'straw', 'sleeve', 'bag']
    results = []

    pkg_matched = 0
    pkg_default = 0

    for _, sale in sales_df.iterrows():
        product_clean = sale['product'].strip().lower()
        size_str = size_map.get(sale['size'], '16oz')
        qty = sale['quantity']

        # Buscar en packaging template
        pkg_row = None
        if packaging_df is not None:
            matches, _ = _find_match(product_clean, packaging_df, 'product_name')
            if matches is not None and len(matches) > 0:
                size_match = matches[matches['size'] == size_str]
                pkg_row = size_match.iloc[0] if len(size_match) > 0 else matches.iloc[0]
                pkg_matched += 1

        if pkg_row is not None:
            for item in packaging_items:
                item_qty = pkg_row.get(item, 0)
                if pd.notna(item_qty) and item_qty > 0:
                    results.append({
                        'store': sale['store'], 'week': sale['week'],
                        'category': 'Plastic goods', 'unit': item,
                        'consumption': item_qty * qty,
                    })
        else:
            # Default: 1 vaso + 1 tapa por bebida (toda bebida lleva vaso y tapa)
            pkg_default += 1
            results.append({'store': sale['store'], 'week': sale['week'],
                            'category': 'Plastic goods', 'unit': 'cup', 'consumption': qty})
            results.append({'store': sale['store'], 'week': sale['week'],
                            'category': 'Plastic goods', 'unit': 'lid', 'consumption': qty})

    total = len(sales_df)
    print(f"  Con template: {pkg_matched}/{total} ({pkg_matched/total*100:.1f}%)")
    print(f"  Con default (1 vaso+tapa): {pkg_default}")

    if not results:
        return pd.DataFrame(columns=['store', 'week', 'category', 'unit', 'consumption'])

    df = pd.DataFrame(results)
    return df.groupby(['store', 'week', 'category', 'unit'])['consumption'].sum().reset_index()

def apply_waste(df_consumption, waste_pct=0.07):
    """
    Aplica % de merma a Dairy y Coffee.
    Plastic goods y Paper goods no tienen merma (los vasos no se desperdician igual).
    """
    df = df_consumption.copy()
    perishable = df['category'].isin(['Dairy', 'Coffee'])
    df['waste'] = 0.0
    df.loc[perishable, 'waste'] = df.loc[perishable, 'consumption'] * waste_pct
    df['consumption_with_waste'] = df['consumption'] + df['waste']
    return df

def calculate_averages(df_consumption):
    """
    Calcula el consumo promedio semanal por tienda-categoría-unidad.
    El promedio es sobre las semanas en que esa categoría tuvo consumo > 0
    (si una semana no aparece, fue una semana sin ventas de ese tipo).
    También retorna el # de semanas con datos para evaluar confiabilidad.
    """
    weeks_per_store = (
        df_consumption.groupby('store')['week']
        .nunique()
        .reset_index(name='weeks_with_data')
    )

    df_avg = (
        df_consumption
        .groupby(['store', 'category', 'unit'])
        .agg(avg_weekly_consumption=('consumption_with_waste', 'mean'))
        .reset_index()
    )

    df_avg = df_avg.merge(weeks_per_store, on='store')
    return df_avg

# ============================================================================
# MAIN SCRIPT
# ============================================================================

def main():
    print("=" * 80)
    print("SUPPLY CONSUMPTION ANALYSIS - LOCAL PROCESSING")
    print("=" * 80)

    # 1. Cargar configuración
    recipes_df = load_recipes()
    classification_df = load_classification()
    packaging_df = load_packaging()

    # 2. Buscar y parsear ventas
    print(f"\nBuscando archivos CSV en: {SALES_FOLDER}")
    sales_files = glob.glob(os.path.join(SALES_FOLDER, "*.csv"))
    if not sales_files:
        print(f"❌ No se encontraron archivos en {SALES_FOLDER}")
        return

    print(f"  ✓ {len(sales_files)} archivos encontrados")
    all_sales = []
    for filepath in sorted(sales_files):
        filename = os.path.basename(filepath)
        print(f"  Procesando {filename}...")
        try:
            sales_data = parse_sales_csv(filepath)
            all_sales.append(sales_data)
            print(f"    ✓ {len(sales_data)} registros de bebidas ({sales_data['store'].nunique()} tiendas)")
        except Exception as e:
            print(f"    ✗ ERROR: {e}")

    df_all_sales = pd.concat(all_sales, ignore_index=True)
    print(f"\n✓ Total: {len(df_all_sales)} registros de bebidas")
    print(f"  Tiendas: {df_all_sales['store'].nunique()}")
    print(f"  Semanas: {df_all_sales['week'].nunique()}")
    print(f"  Productos únicos: {df_all_sales['product'].nunique()}")

    # 3. Calcular consumo de ingredientes (Coffee, Dairy, Paper goods)
    df_ingredients = calculate_consumption(df_all_sales, recipes_df, classification_df)

    # 4. Calcular packaging (vasos, tapas, mangas, pajitas, servilletas)
    #    Esto reemplaza la lógica anterior de contar cups desde Ice Cubes en recetas,
    #    que era incorrecta (los hot drinks no tienen Ice Cubes y quedaban con 0 vasos).
    df_packaging = calculate_packaging_consumption(df_all_sales, packaging_df)

    # 5. Unir ingredientes y packaging (sin duplicar Plastic goods de recetas)
    df_ingredients_no_plastic = df_ingredients[
        df_ingredients['category'] != 'Plastic goods'
    ].copy()
    df_consumption = pd.concat(
        [df_ingredients_no_plastic, df_packaging], ignore_index=True
    )

    # 6. Aplicar merma
    print("\nAplicando merma/waste (7%) a Dairy y Coffee...")
    df_consumption = apply_waste(df_consumption, WASTE_PERCENTAGE)

    # 7. Calcular promedios semanales
    print("Calculando promedios semanales...")
    df_averages = calculate_averages(df_consumption)

    # 8. Guardar CSVs
    df_consumption.to_csv('consumption_by_store_week.csv', index=False)
    df_averages.to_csv('consumption_summary.csv', index=False)
    print("\n✓ consumption_by_store_week.csv")
    print("✓ consumption_summary.csv")

    # 9. Generar reporte
    _write_report(df_averages, df_all_sales, sales_files)
    print("✓ analysis_report.txt")

    # 10. Resumen en pantalla
    print("\n" + "=" * 80)
    print("RESUMEN POR CATEGORÍA (promedio entre tiendas confiables)")
    print("=" * 80)
    reliable = df_averages[df_averages['weeks_with_data'] >= MIN_RELIABLE_WEEKS]
    summary = (
        reliable
        .groupby(['category', 'unit'])['avg_weekly_consumption']
        .mean()
        .reset_index()
    )
    for _, row in summary.iterrows():
        val, unit_out = to_order_unit(row['avg_weekly_consumption'], row['unit'])
        display = CATEGORY_DISPLAY.get((row['category'], row['unit']),
                                       f"{row['category']} ({unit_out})")
        print(f"  {display:<40}: {val:>10.2f} {unit_out}/semana")

    unreliable = df_averages[df_averages['weeks_with_data'] < MIN_RELIABLE_WEEKS]['store'].unique()
    if len(unreliable) > 0:
        print(f"\n⚠️  Tiendas excluidas del promedio (< {MIN_RELIABLE_WEEKS} semanas de datos):")
        for s in sorted(unreliable):
            weeks = df_averages[df_averages['store'] == s]['weeks_with_data'].iloc[0]
            print(f"     {s} ({weeks} semana(s))")

def _write_report(df_averages, df_all_sales, sales_files):
    with open('analysis_report.txt', 'w', encoding='utf-8') as f:
        f.write("SUPPLY CONSUMPTION ANALYSIS REPORT\n")
        f.write("=" * 80 + "\n\n")
        f.write(f"Fecha de análisis: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"Archivos procesados: {len(sales_files)}\n")
        f.write(f"Tiendas: {df_all_sales['store'].nunique()}\n")
        f.write(f"Semanas con datos: {df_all_sales['week'].nunique()}\n\n")

        f.write("NOTA SOBRE UNIDADES\n")
        f.write("-" * 80 + "\n")
        f.write("  Café Espresso/Molido → kg  (se ordena por saco/bolsa en kg)\n")
        f.write("  Cold Brew / Drip Coffee → L  (se ordena por bidón o se prepara en tienda)\n")
        f.write("  Lácteos Líquidos → L  (leche, almond milk, coconut milk)\n")
        f.write("  Lácteos Sólidos → kg  (cream cheese, mantequilla)\n")
        f.write("  Packaging → unidades  (vasos, tapas, mangas, pajitas, servilletas)\n\n")

        f.write("RESUMEN POR TIENDA\n")
        f.write("-" * 80 + "\n\n")

        for store in sorted(df_averages['store'].unique()):
            store_data = df_averages[df_averages['store'] == store]
            weeks = store_data['weeks_with_data'].iloc[0]
            reliable = "(confiable)" if weeks >= MIN_RELIABLE_WEEKS else f"⚠️  SOLO {weeks} SEMANA(S) - DATOS INSUFICIENTES"

            f.write(f"Tienda: {store}\n")
            f.write(f"  Semanas de datos: {weeks} {reliable}\n\n")

            # Agrupar por sección de compra
            sections = [
                ('INGREDIENTES CAFÉ', ['Coffee']),
                ('LÁCTEOS', ['Dairy']),
                ('PACKAGING', ['Plastic goods', 'Paper goods']),
            ]

            for section_name, categories in sections:
                section_data = store_data[store_data['category'].isin(categories)]
                if len(section_data) == 0:
                    continue
                f.write(f"  {section_name}:\n")
                for _, row in section_data.iterrows():
                    val, unit_out = to_order_unit(row['avg_weekly_consumption'], row['unit'])
                    display = CATEGORY_DISPLAY.get(
                        (row['category'], row['unit']),
                        f"{row['category']} ({unit_out})"
                    )
                    f.write(f"    {display:<40}: {val:>10.2f} {unit_out}/semana\n")
                f.write("\n")

            f.write("\n")

if __name__ == "__main__":
    main()
