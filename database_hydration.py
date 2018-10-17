import os, sys, subprocess, yaml
import psycopg2
from psycopg2.extras import DictCursor


class hydrate_database:

	def __init__(self, db_connection, db_cursor):
		self.db_connection = db_connection
		self.db_cursor = db_cursor

	def region_definitions(self, data_dir):
		'''
		imports regions into database
		'''
		# set location to search for data
		dir_name = 'region_definitions'

		# get list of files in directory
		dir_contents = os.listdir(os.path.join(data_dir, dir_name))

		# if no files, return to main function
		if len(dir_contents) == 0: return

		# loop through the directory
		for item in dir_contents:

			# if item is a file skip it
			if os.path.isfile(os.path.join(data_dir, dir_name, item)):
				continue

			# add the directory as a region set
			subprocess.run(['psql', '-U', 'vagrant', '-d', 'nismod_smif', '-c','INSERT INTO region_sets (name) VALUES (\'%s\');' % (item)])

		return


	def interval_definitions(self, data_dir):
		'''
		Insert into the interval_sets and interval tables any interval data within the interval_definitions folder
		'''
		# set location to search for data
		dir_name = 'interval_definitions'

		# get list of files in directory
		dir_contents = os.listdir(os.path.join(data_dir, dir_name))

		# if no files, return to main function
		if len(dir_contents) == 0: return

		# loop through the directory
		for item in dir_contents:

			# if item is not a file skip it
			if os.path.isfile(os.path.join(data_dir, dir_name, item)) is False:
				continue

			# need to read the first line of the file to get the column order
			file = open(os.path.join(data_dir, dir_name, item))
			columns = file.readline().replace('\n', '').split(',')

			# create a comma separated string of column order
			file_columns = ''
			for col in columns:
				file_columns += '"%s",' % (col)

			# get name to be used in database
			name = item.split('.')[0]

			# add the file as an interval set using the file name returning the id of the set
			self.db_cursor.execute('INSERT INTO interval_sets (name) VALUES (%s) RETURNING id;', [name.replace("'",'')])
			self.db_connection.commit()
			set_id = self.db_cursor.fetchone()[0]

			# create temporary table to copy data into, copy data into temp table, copy data from temp to full table with id from the interval_sets table
			subprocess.run(['psql', '-U', 'vagrant', '-d', 'nismod_smif', '-c',
							'CREATE TEMPORARY TABLE intervals_temp ("id" varchar, "start" varchar, "end" varchar); COPY intervals_temp(%s) FROM \'%s\' DELIMITER \',\' CSV	HEADER; INSERT INTO intervals SELECT "id","start","end",(SELECT id FROM interval_sets WHERE name = \'%s\' LIMIT 1) FROM intervals_temp;' % (file_columns[:-1], os.path.join('/vagrant', data_dir, dir_name, item), name)])

		return


	def interventions(self, data_dir):
		"""
		import interventions into the database
		two step procedure
		  (1) add to the interventions relation (for quick access)
		  (2) add to the interventions_sector relation (for detailed queries)
				- for each attribute check a column exists & if not add
		"""
		# set location to search for data
		dir_name = 'interventions'

		# get list of files in directory
		dir_contents = os.listdir(os.path.join(data_dir, dir_name))

		# if no files, return to main function
		if len(dir_contents) == 0: return

		# loop through the directory
		for item in dir_contents:

			# if item is not a file skip it
			if os.path.isfile(os.path.join(data_dir, dir_name, item)) is False:
				continue

			# open yml file and get data
			with open(os.path.join(data_dir, dir_name, item), 'r') as stream:
				data = yaml.load(stream)

			# get sector name
			sector_name = item.split('.')[0]
			if 'interventions' in sector_name:
				sector_name = sector_name.replace('_interventions', '')

			# get sector id from sectors relation

			self.db_cursor.execute('SELECT id FROM sectors WHERE names=%s', [sector_name])
			sector_id = self.db_cursor.fetchone()

			# loop through the interventions
			for intervention in data:

				# add intervention to the interventions relation


				# add to the sector specific interventions relation

				# need to add in checking for column names and adding them if missing

				#print(intervention)
				# get column list from yml
				# get value list
				column_list = ''
				value_list = ''

				# loop through the keys for the intervention
				for key in intervention.keys():
					#print(key)
					# check if an attribute, or if a dictionary with sub-attributes
					if isinstance(intervention[key], dict):
						#print('procesed as a dict')
						# loop through the sub-attributes
						for subkey in intervention[key].keys():

							# form column list
							column_list += "%s," % (str(key) + '_' + str(subkey))

							# form value list
							if isinstance(intervention[key][subkey], str):
								value_list += "'%s'," % (intervention[key][subkey])
							else:
								value_list += "%s," % (intervention[key][subkey])

					else:
						#print('processing here')
						# if this is changed to false don't add column of value to insert statement
						add_column = True

						# form value list
						if isinstance(intervention[key], str): #if value a string
							value_list += "'%s'," % (intervention[key])
						elif isinstance(intervention[key], list): #if value a list
							text = str(intervention[key]).replace('[','{').replace(']','}')
							value_list += "'%s'," % (text)
						elif intervention[key] is None: #if value is NoneType
							add_column = False
						else: #if value is anything else eg. a number
							#print('processed as something else')
							#print(type(intervention[key]))
							value_list += "%s," % (intervention[key])

						# form column list
						if add_column is True:
							column_list += "%s," % key

				# check if intervention type is in the types table - if not add
				# this will need updating depending on what format the initial conditions will be uploaded in
				subprocess.run(['psql', '-U', 'vagrant', '-d', 'nismod_smif', '-c', 'INSERT INTO intervention_type (name) SELECT (\'%s\') WHERE NOT EXISTS (SELECT id FROM intervention_type WHERE name=\'%s\');' % (intervention['name'], intervention['name'])])

				# insert intervention into interventions table - get intervention type id and add to table
				subprocess.run(['psql', '-U', 'vagrant', '-d', 'nismod_smif', '-c','INSERT INTO %s (%s,sector,intervention_type_id) VALUES (%s,(SELECT id FROM sectors WHERE name=\'%s\'),(SELECT id FROM intervention_type WHERE name=\'%s\'));' % ('interventions_'+sector_name,column_list[:-1], value_list[:-1], sector_name, intervention['name'])])

		return


	def initial_conditions(self, data_dir):
		'''
		populated tables with the initial conditions data (initial interventions?)
		populates the interventions table
			- each file equates to a set
			- the set should be added to interventions_planned
			- should use the interventions function
		'''
		# set location to search for data
		dir_name = 'initial_conditions'

		# get list of files in directory
		dir_contents = os.listdir(os.path.join(data_dir, dir_name))

		# if no files, return to main function
		if len(dir_contents) == 0: return

		# loop through the directory
		for item in dir_contents:

			# if item is not a file skip it
			if os.path.isfile(os.path.join(data_dir, dir_name, item)) is False:
				continue

			# open yml file and get data
			with open(os.path.join(data_dir, dir_name, item), 'r') as stream:
				data = yaml.load(stream)

			# get sector name
			sector_name = item.split('.')[0]

			# loop through the conditions
			for condition in data:

				# get column list from yml
				# get value list
				column_list = ''
				value_list = ''
				existing_int_col_list = ''
				existing_int_val_list = ''

				# loop through the keys for the conditions
				for key in condition.keys():

					# check if an attribute, or if a dictionary with sub-attributes
					if isinstance(condition[key], dict):

						# loop through the sub-attributes
						for subkey in condition[key].keys():

							# form column list
							column_list += "%s," % (str(key) + '_' + str(subkey))

							# form value list
							if isinstance(condition[key][subkey], str):
								value_list += "'%s'," % (condition[key][subkey])
							else:
								value_list += "%s," % (condition[key][subkey])

					else:
						# build date not part of interventions table so in different list for second part of hydration process
						if key == 'build_date':
							# form column list
							existing_int_col_list += "%s," % key

							# form value list
							if isinstance(condition[key], str):
								existing_int_val_list += "'%s'," % (condition[key])
							else:
								existing_int_val_list += "%s," % (condition[key])
						else:
							# form column list
							column_list += "%s," % key

							# form value list
							if isinstance(condition[key], str):
								value_list += "'%s'," % (condition[key])
							else:
								value_list += "%s," % (condition[key])

				# insert intervention into interventions table
				subprocess.run(['psql', '-U', 'vagrant', '-d', 'nismod_smif', '-c','INSERT INTO interventions (%s,sector) VALUES (%s,(SELECT id FROM sectors WHERE name=\'%s\'));' % (column_list[:-1], value_list[:-1], sector_name)])

				# insert id into existing interventions table (get id from sequence)
				subprocess.run(['psql', '-U', 'vagrant', '-d', 'nismod_smif', '-c','INSERT INTO interventions_existing (%s,intervention_id) VALUES (%s,(SELECT max(id) FROM interventions));' % (existing_int_col_list[:-1], existing_int_val_list[:-1])])

		return


	def base_data_hydration(self, data_dir):
		'''
		add base data to database
		'''
		# set location to search for data
		dir_name = 'data'

		# get list of files in directory
		dir_contents = os.listdir(os.path.join(data_dir))
		print(dir_contents)
		# if no files, return to main function
		if len(dir_contents) == 0: return

		# loop through the directory
		for item in dir_contents:

			# if item is not a file skip it
			if os.path.isfile(os.path.join(data_dir, item)) is False:
				continue

			# hydrate with units file
			if item == 'units.txt':
				file = open(os.path.join(data_dir, item))

				# get name of file - also name of table
				name = item.split('.')[0]

				# insert into table line by line
				# read each line in file
				for line in file:
					# split the line on the first '=' s
					unit, description = line.split('=', 1)
					subprocess.run(['psql', '-U', 'vagrant', '-d', 'nismod_smif', '-c',
									'INSERT INTO %s (unit, description) VALUES (\'%s\',\'%s\');' % (
										name, unit, description)])

			elif item == 'sectors.txt':
				file = open(os.path.join(data_dir, item))

				# get name of file - also name of table
				name = item.split('.')[0]

				# insert into table line by line
				# read each line in file
				for line in file:
					# split the line on the first '=' s
					subprocess.run(['psql', '-U', 'vagrant', '-d', 'nismod_smif', '-c',
									'INSERT INTO %s (name) VALUES (\'%s\');' % (
										name, line.strip())])

			else:
				# need to read the first line of the file to get the column order
				file = open(os.path.join(data_dir, item))
				columns = file.readline().replace('\n', '').split(',')

				# create a comma separated string of column order
				file_columns = ''
				for col in columns:
					file_columns += '"%s",' % (col)

				# get name of file - also name of table
				name = item.split('.')[0]

				# copy data to database table
				subprocess.run(['psql', '-U', 'vagrant', '-d', 'nismod_smif', '-c',
								'COPY %s(%s) FROM \'%s\' DELIMITER \',\' CSV	HEADER;' % (
								name, file_columns[:-1], os.path.join('/vagrant', data_dir, item))])

		return


def main():
	'''
	Data_path: should be the location where all the data for populating the database is stored.
	It should match the defined structure.
	--data_path
		|--initial_conditions
			|--file1.yml
			|--file2.yml
	    |--interval_definitions
	    	|--file1.csv
	    	|--file2.csv
	    |--interventions
	    	|--file1.yml
	    	|--file2.yml
	    |--narratives
	    |--region_definitions
			|--region_set_1
			|--region_set_2
	    |--scenarios
	    |--data
	    	|--file1.csv
	    	|--file2.csv

	'''
	# get data path from passed arguments
	data_path = sys.argv[1]

	# create database connection and cursor
	db_connection = psycopg2.connect("host=localhost dbname=nismod_smif user=vagrant password=vagrant")
	db_cursor = db_connection.cursor(cursor_factory=psycopg2.extras.DictCursor)

	# run database hydration for base database data
	base_data_hydration(data_path)

	# run database hydration for region definitions
	# only reads in the file names at the moment
	#region_definitions(data_path)
	region_definitions(data_path)

	# run database hydration for interval definitions
	interval_definitions(data_path, db_connection, db_cursor)

	# run database hydration for interventions
	interventions(data_path)

	# run database hydration for initial conditions
	# this does not work
	initial_conditions(data_path)

	# close database connection
	db_connection.close()

main()