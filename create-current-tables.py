#!/usr/bin/env python3

import sqlite3

print('Creating current tables')

db = __import__('import-clinvar-xml').connect()
cursor = db.cursor()

cursor.execute('DROP TABLE IF EXISTS current_submissions')

cursor.execute('''
    CREATE TABLE current_submissions AS
    SELECT * FROM submissions WHERE date=(
        SELECT MAX(date) FROM submissions
    )
''')

cursor.execute('CREATE INDEX current_submissions__variant_name ON current_submissions (variant_name)')
cursor.execute('CREATE INDEX current_submissions__rsid ON current_submissions (rsid)')
cursor.execute('CREATE INDEX current_submissions__gene ON current_submissions (gene)')
cursor.execute('CREATE INDEX current_submissions__normalized_gene ON current_submissions (normalized_gene)')
cursor.execute('CREATE INDEX current_submissions__rcv ON current_submissions (rcv)')
cursor.execute('CREATE INDEX current_submissions__scv ON current_submissions (scv)')
cursor.execute('CREATE INDEX current_submissions__submitter_id ON current_submissions (submitter_id)')
cursor.execute('CREATE INDEX current_submissions__submitter_name ON current_submissions (submitter_name)')
cursor.execute('CREATE INDEX current_submissions__submitter_country_code ON current_submissions (submitter_country_code)')
cursor.execute('CREATE INDEX current_submissions__significance ON current_submissions (significance)')
cursor.execute('CREATE INDEX current_submissions__condition_name ON current_submissions (condition_name)')
cursor.execute('CREATE INDEX current_submissions__condition_xrefs ON current_submissions (condition_xrefs)')
cursor.execute('CREATE INDEX current_submissions__method ON current_submissions (method)')

cursor.execute('DROP TABLE IF EXISTS current_comparisons')

cursor.execute('''
    CREATE TABLE current_comparisons AS
    SELECT * FROM comparisons WHERE date=(
        SELECT MAX(date) FROM comparisons
    )
''')

cursor.execute('CREATE INDEX current_comparisons__variant_name ON current_comparisons (variant_name)')
cursor.execute('CREATE INDEX current_comparisons__gene ON current_comparisons (gene)')
cursor.execute('CREATE INDEX current_comparisons__gene_type ON current_comparisons (gene_type)')
cursor.execute('CREATE INDEX current_comparisons__normalized_gene ON current_comparisons (normalized_gene)')
cursor.execute('CREATE INDEX current_comparisons__normalized_gene_type ON current_comparisons (normalized_gene_type)')
cursor.execute('CREATE INDEX current_comparisons__submitter1_id ON current_comparisons (submitter1_id)')
cursor.execute('CREATE INDEX current_comparisons__submitter1_name ON current_comparisons (submitter1_name)')
cursor.execute('CREATE INDEX current_comparisons__submitter1_country_code ON current_comparisons (submitter1_country_code)')
cursor.execute('CREATE INDEX current_comparisons__scv1 ON current_comparisons(scv1)')
cursor.execute('CREATE INDEX current_comparisons__significance1 ON current_comparisons (significance1)')
cursor.execute('CREATE INDEX current_comparisons__normalized_significance1 ON current_comparisons (normalized_significance1)')
cursor.execute('CREATE INDEX current_comparisons__star_level1 ON current_comparisons (star_level1)')
cursor.execute('CREATE INDEX current_comparisons__condition1_name ON current_comparisons (condition1_name)')
cursor.execute('CREATE INDEX current_comparisons__method1 ON current_comparisons (method1)')
cursor.execute('CREATE INDEX current_comparisons__normalized_method1 ON current_comparisons (normalized_method1)')
cursor.execute('CREATE INDEX current_comparisons__submitter2_id ON current_comparisons (submitter2_id)')
cursor.execute('CREATE INDEX current_comparisons__significance2 ON current_comparisons (significance2)')
cursor.execute('CREATE INDEX current_comparisons__normalized_significance2 ON current_comparisons (normalized_significance2)')
cursor.execute('CREATE INDEX current_comparisons__star_level2 ON current_comparisons (star_level2)')
cursor.execute('CREATE INDEX current_comparisons__normalized_method2 ON current_comparisons (normalized_method2)')
cursor.execute('CREATE INDEX current_comparisons__condition2_name ON current_comparisons (condition2_name)')
cursor.execute('CREATE INDEX current_comparisons__conflict_level ON current_comparisons (conflict_level)')

def create_gene_links_table(normalized):
    if normalized:
        table = 'normalized_gene_links'
        gene_column = 'normalized_gene'
        type_column = 'normalized_gene_type'
    else:
        table = 'gene_links'
        gene_column = 'gene'
        type_column = 'gene_type'

    cursor.execute('DROP TABLE IF EXISTS ' + table)

    cursor.execute('CREATE TABLE ' + table + ' (gene TEXT, see_also TEXT)')

    gene_combinations = list(map(
        lambda row: row[0],
        cursor.execute('SELECT DISTINCT ' + gene_column + ' FROM current_submissions WHERE ' + type_column + '=2')
    ))
    for gene_combination in gene_combinations:
        for individual_gene in gene_combination.split(', '):
            is_gene = bool(list(cursor.execute(
                'SELECT 1 FROM current_submissions WHERE ' + gene_column + '=? LIMIT 1', [individual_gene]
            )))
            if is_gene:
                cursor.executemany(
                    'INSERT INTO ' + table + ' VALUES (?,?)',
                    [[gene_combination, individual_gene], [individual_gene, gene_combination]]
                )

    cursor.execute('CREATE INDEX ' + table + '__gene ON ' + table + ' (gene)')

create_gene_links_table(True)
create_gene_links_table(False)

db.commit()
db.close()
