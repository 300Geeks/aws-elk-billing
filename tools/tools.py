import os, sys
sys.path.append(os.path.join(os.path.dirname(__file__)))
'''
added head source directory in path for import from any location and relative testing and pwd for open() relative files
'''
import pyelasticsearch as pyes
import boto3
import time
import socket
import sys
from datetime import datetime as dtdt
from datetime import date as dtd
from dateutil.relativedelta import relativedelta
import subprocess
import os

class Tools:
    
    def __init__(self, s3=None):
        if s3:
            self.bucketname = os.environ['S3_BUCKET_NAME']
            self.path_name_s3_billing = [ i for i in os.environ['S3_REPORT_PATH'].split('/') if i]
            self.s3_report_name = os.environ['S3_REPORT_NAME']
            self.s3 = s3
        else:
            pass

    def check_elk_connection(self):
        elasticsearch_socket = socket.socket()
        logstash_socket = socket.socket()
        kibana_socket = socket.socket()
        connection_ok = False
        for _ in range(15):
            try:
                print 'Checking if Elasticsearch container has started to listen to 9200'
                elasticsearch_socket.connect(('elasticsearch', 9200))
                print 'Great Elasticsearch is listening on 9200, 9300 :)'
                connection_ok = True
                break
            except Exception as e:
                print(
                    "Something's wrong with Elasticsearch. Exception is %s" % (e))
                print 'I will retry after 4 seconds'
                connection_ok = True
                time.sleep(4)

        for _ in range(15):
            try:
                print 'Checking if Logstash container has started to listen to 5140'
                logstash_socket.connect(('logstash', 5140))
                print 'Great Logstash is listening on 5140 :)'
                connection_ok = True
                break
            except Exception as e:
                print("Something's wrong with Logstash. Exception is %s" % (e))
                print 'I will retry after 4 seconds'
                connection_ok = True
                time.sleep(4)

        for _ in range(15):
            try:
                print 'Checking if Kibana container has started to listen to 5601'
                kibana_socket.connect(('kibana', 5601))
                print 'Great Kibana is listening on 5601 :)'
                connection_ok = True
                break
            except Exception as e:
                print("Something's wrong with Kibana. Exception is %s" % (e))
                print 'I will retry after 4 seconds'
                connection_ok = True
                time.sleep(4)

        elasticsearch_socket.close()
        logstash_socket.close()
        kibana_socket.close()
        
        return connection_ok

    def index_template(self):
        out = subprocess.check_output(['curl -XHEAD -i "elasticsearch:9200/_template/aws_billing"'], shell=True, stderr=subprocess.PIPE)
        if '200 OK' not in out:
            status = subprocess.Popen(
                ['curl -XPUT elasticsearch:9200/_template/aws_billing -d "`cat /aws-elk-billing/aws-billing-es-template.json`"'],
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if status.wait() != 0:
                print 'Something went wrong while creating mapping index'
                sys.exit(1)
            else:
                print 'ES mapping created :)'
        else:
            print 'Template already exists'

    def get_s3_bucket_dir_to_index(self):
        if len(self.path_name_s3_billing) == 1:
            prefix = '/' + '/'.join(self.path_name_s3_billing) + '/'
        else:
            prefix='/'.join(self.path_name_s3_billing) + '/'

        key_names = self.s3.list_objects(
				Bucket=self.bucketname,
				Prefix=prefix,
				Delimiter='/')
        s3_dir_names = []

        if 'CommonPrefixes' not in key_names:
            return 1

        for keys in key_names['CommonPrefixes']:
            s3_dir_names.append(keys['Prefix'].split('/')[-2])

        s3_dir_names.sort()
        es = pyes.ElasticSearch('http://elasticsearch:9200')
        index_list = es.get_mapping('aws-billing*').keys()
        index_time = []
        for i in index_list:
            if i:
                index_time.append(es.search(index=i, size=1, query={"query": {"match_all": {}}})[
                                  'hits']['hits'][0]['_source']['@timestamp'])

        index_time.sort(reverse=True)

        dir_start = 0
        dir_end = None

        if index_time:
            current_dir = dtd.today().strftime('%Y%m01') + '-' + (dtd.today() + \
                                    relativedelta(months=1)).strftime('%Y%m01')

            last_ind_dir = index_time[0].split('T')[0].replace('-', '')
            last_ind_dir = dtdt.strptime(last_ind_dir, '%Y%m%d').strftime('%Y%m01') + '-' + (
                dtdt.strptime(last_ind_dir, '%Y%m%d') + relativedelta(months=1)).strftime('%Y%m01')
            dir_start = s3_dir_names.index(last_ind_dir)
            dir_end = s3_dir_names.index(current_dir) + 1

        s3_dir_to_index = s3_dir_names[dir_start:dir_end]
        print('Months to be indexed: {}'.format(', '.join(s3_dir_to_index)))
        # returning only the dirnames which are to be indexed
        return  s3_dir_to_index
    
    def get_latest_zip_filename(self, monthly_dir_name):
        # monthly_dir_name for aws s3 directory format for getting the correct json file
        # json file name
        if len(self.path_name_s3_billing) == 1:
            latest_json_file_name = '/' + '/'.join(self.path_name_s3_billing + [monthly_dir_name, self.s3_report_name + '-Manifest.json'])
        else:
            latest_json_file_name = '/'.join(self.path_name_s3_billing + [monthly_dir_name, self.s3_report_name + '-Manifest.json'])

        # download the jsonfile as getfile_$time.json from s3
        print('Downloading {}...'.format(latest_json_file_name))
        self.s3.download_file(
            self.bucketname,
            latest_json_file_name,
            'getfile.json')

        # read the json file to get the latest updated version of csv
        f = open('getfile.json', 'r')
        content = eval(f.read())
        latest_gzip_filename = content['reportKeys'][0]
        f.close()
        return latest_gzip_filename

    def get_req_csv_from_s3(self, monthly_dir_name, latest_gzip_filename):
        # the local filename formated for compatibility with the go lang code billing_report_yyyy-mm.csv
        local_gz_filename = 'billing_report_' + \
            dtdt.strptime(monthly_dir_name.split('-')[0], '%Y%m%d').strftime('%Y-%m') + '.csv.gz'
        local_csv_filename = local_gz_filename[:-3]

        # downloading the zipfile from s3
        self.s3.download_file(self.bucketname, latest_gzip_filename, local_gz_filename)

        # upzip and replace the .gz file with .csv file
        print("Extracting latest csv file")
        process_gunzip = subprocess.Popen(['gunzip -v ' + local_gz_filename], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        
        return local_csv_filename

    def index_csv(self, filename, dir_name):

        # DELETE earlier aws-billing* index if exists for the current indexing month
        # current month index format (name)
        index_format = dtdt.strptime(
            dir_name.split('-')[0],
            '%Y%m%d').strftime('%Y.%m')
        os.environ['file_y_m'] = index_format
        # have to change the name of the index in logstash index=>indexname

        status = subprocess.Popen(
            ['curl -XDELETE elasticsearch:9200/aws-billing-' + index_format], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if status.wait() != 0:
            print 'I think there are no aws-billing* indice or it is outdated, its OK main golang code will create a new one for you :)'
        else:
            print 'aws-billing* indice deleted or Not found, its OK main golang code will create a new one for you :)'

        # Run the main golang code to parse the billing file and send it to
        # Elasticsearch over Logstash
        status = subprocess.Popen(
            ['go run /aws-elk-billing/main.go --file /aws-elk-billing/' + filename], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        print(status.stdout.read())
        print(status.stderr.read())
        if status.wait() != 0:
            print 'Something went wrong while getting the file reference or while talking with logstash'
            sys.exit(1)
        else:
            print 'AWS Billing report sucessfully parsed and indexed in Elasticsearch via Logstash :)'

    def index_kibana(self):
        # Index the search mapping for Discover to work 
        status = subprocess.Popen(
                ['(cd /aws-elk-billing/kibana; bash orchestrate_search_mapping.sh)'],
                shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if status.wait() != 0:
            print 'The Discover Search mapping failed to be indexed to .kibana index in Elasticsearch'
            sys.exit(1)
        else:
            print 'The Discover Search mapping sucessfully indexed to .kibana index in Elasticsearch, Kept intact if user already used it :)'


        # Index Kibana dashboard
        status = subprocess.Popen(
            ['(cd /aws-elk-billing/kibana; bash orchestrate_dashboard.sh)'],
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if status.wait() != 0:
            print 'AWS-Billing-DashBoard default dashboard failed to indexed to .kibana index in Elasticsearch'
            sys.exit(1)
        else:
            print 'AWS-Billing-DashBoard default dashboard sucessfully indexed to .kibana index in Elasticsearch, Kept intact if user already used it :)'

        # Index Kibana visualization
        status = subprocess.Popen(
            ['(cd /aws-elk-billing/kibana; bash orchestrate_visualisation.sh)'],
            shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if status.wait() != 0:
            print 'Kibana default visualizations failed to indexed to .kibana index in Elasticsearch'
            sys.exit(1)
        else:
            print 'Kibana default visualizations sucessfully indexed to .kibana index in Elasticsearch, Kept intact if user have already used it :)'

    def delete_csv_json_files(self):
        # delete all getfile json, csv files and part downloading files after indexing over
        process_delete_csv = subprocess.Popen(
            ["find /aws-elk-billing -name 'billing_report_*' -exec rm -f {} \;"], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        process_delete_json = subprocess.Popen(
            ["find /aws-elk-billing -name 'getfile*' -exec rm -f {} \;"], shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
