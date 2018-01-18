## Install

• clone the repo

    cd <GIT root directory>
    git clone https://github.com/<GITHUB_USER_NAME>/opus.git

• update the pds-tools submodule

    cd opus
    git submodule init
    git submodule update

• install mysql if necessary (Ubuntu)

    apt-get install mysql-server
    apt-get install mysql-client
    apt-get install libmysqlclient-dev
	
• create a virtualenv and install the dependencies

    NOTE: Ubuntu requires that the following package be installed first:
        apt-get install libncurses-dev
	
    virtualenv --python=<YOUR PYTHON 2.7 EXECUTABLE> venv --distribute
    source venv/bin/activate
    pip install -r requirements.txt

• create the mysql databases

    # Run the mysql command line:
    mysql -p
    
    # From within mysql, commands to create the 3 opus databases:  
    create database opus_small;  
    create database dictionary;
    create database opus_metrics;

• initialize the databases from dump files (ask Rings Node for these files)

    mysql opus_small < opus_small.sql -p
    mysql dictionary < dictionary.empty.sql -p
    mysql opus_metrics < opus_metrics.empty.sql -p

• edit the secrets.py file

    cp secrets_template.py secrets.py
    
    - Change DB_USER to your mysql user
    - Change DB_PASS to your mysql password
    - Change SECRET_KEY to a unique key (generators are available on the web)
    - Change TAR_FILE_PATH to a directory where "shopping cart" tar files can be stored
    - Change FILE_PATH to the location of the Cassini data volumes
    - Change DERIVED_PATH to the location of the Cassini calibrated data volumes
    - Change IMAGE_PATH to the location of the Cassini browse images

    For example:
        FILE_PATH  = '/seti/external/cassini/volumes/COISS_2xxx/'
        DERIVED_PATH  = '/seti/external/cassini/derived/COISS_2xxx/'
        IMAGE_PATH = '/seti/external/cassini/browse/COISS_2xxx/'
	
• edit the settings_local.py file

    cp settings_local_example.py settings_local.py
    
    - Provide the full path to the OPUS Django directory

• Make the logs directory

    mkdir logs

• To run the tests or server for the first time you may need to run migrate (try them first and see):

	If you are starting with a dump of an opus database, drop the following tables for the migrate to work:

	  mysql commands:
	  
	  drop table django_admin_log;
	  drop table django_content_type;
	  drop table django_session;
	  drop table django_site;

    python manage.py migrate

• Run the tests

    python manage.py test apps

  Note the tests run against the same database as the app. Ignore errors about missing files, ObsMovies, or ObsMissionHubble

• Run the webserver

	python manage.py runserver



## Dependencies

see requirements.txt

memcached daemon must be running on production server OR if it's not then comment out the cache_backend line in settings.py

see also apps/README.md (probably deprecated)

some possibly useful tidbits are also in the /doc directory )
