import os
import re
import sys
import json
import urllib2
import base64

def main():
	get_vcap_config()
	appinfo = get_application_info()
	# service = find_spring_config_service(appinfo)
	# if service != None:
	# 	get_spring_cloud_config(service, appinfo)

def detect():
	appinfo = get_application_info()
	service = find_edgemicro_service(appinfo)
	if service == None:
		sys.exit(1)
	print 'edgemicro-config'

def compile():
	appinfo = get_application_info()
	service = find_edgemicro_service(appinfo)
	# print >> sys.stderr, "service-config:"
	# json.dump(service, sys.stderr, indent=4)
	# print >> sys.stderr
	if service == None:
		sys.exit(1)
	print '-o %s -e %s -u %s -p %s' % (service["credentials"]["org"], service["credentials"]["env"], service["credentials"]["user"], service["credentials"]["pass"])

def getOrgEnv():
	appinfo = get_application_info()
	service = find_edgemicro_service(appinfo)
	if service == None:
		sys.exit(1)
	print '-o %s -e %s' % (service["credentials"]["org"], service["credentials"]["env"])

def getAppName():
	appinfo = get_application_info()
	# json.dump(appinfo, sys.stderr, indent=4)
	print appinfo['uris'][0].replace('.', '-')

def updateSpikeArrest():
	appinfo = get_application_info()
	service = find_edgemicro_service(appinfo)
	if service == None:
		sys.exit(1)
	creds = service.get("credentials")
	updateMicroConfig(creds.get("timeunit", "minute"), creds.get("allow", "30"))

def updateMicroConfig(timeunit, allow):
	timeunitPattern = re.compile(r"""(timeUnit: )(\w+)""", re.MULTILINE)
	allowPattern = re.compile(r"""(allow: )(\w+)""", re.MULTILINE)
	buildpath = os.environ['BUILD_DIR']
	yamlfile = os.path.join(buildpath,'apigee_edge_micro','apigee-edge-micro-2.0.4','config','default.yaml')
	data = file(yamlfile,'r').read()
	data = timeunitPattern.sub(r'\g<1>' + timeunit, data)
	data = allowPattern.sub(r'\g<1>' + allow, data)
	file(yamlfile,'w').write(data)


vcap_config = None
log_level = 1

def get_vcap_config():
	global vcap_config
	global log_level
	vcap_config = json.loads(os.getenv('VCAPX_CONFIG', '{}'))
	log_level = vcap_config.get('loglevel', 1)

# Get Application Info
#
# Certain information about the application is used in
# the query to the configuration servers, to allow them
# to return config values dependent on the application
# instance deployment
#
def get_application_info():
	appinfo = {}
	vcap_application = json.loads(os.getenv('VCAP_APPLICATION', '{}'))
	appinfo['name'] = vcap_application.get('application_name')
	if appinfo['name'] == None:
		print >> sys.stderr, "VCAP_APPLICATION must specify application_name"
		sys.exit(1)
	appinfo['profile'] = vcap_application.get('space_name', 'default')
	appinfo['uris'] = vcap_application.get('uris')
	return appinfo

# Find bound configuration service
#
# We only read configuration from bound config services that
# are appropriately tagged. And since, for user-provided services,
# tags can only be set inside the credentials dict, not in the
# top-level one, we check for tags in both places.
#
def find_edgemicro_service(appinfo):
	vcap_services = json.loads(os.getenv('VCAP_SERVICES', '{}'))
	for service in vcap_services:
		service_instances = vcap_services[service]
		for instance in service_instances:
			tags = instance.get('tags', []) + instance.get('credentials',{}).get('tags',[])
			if 'edgemicro' in tags:
				return instance
	return None

def get_access_token(credentials):
	client_id = credentials.get('client_id','')
	client_secret = credentials.get('client_secret','')
	access_token_uri = credentials.get('access_token_uri')
	if access_token_uri is None:
		return None
	req = urllib2.Request(access_token_uri)
	req.add_header('Authorization', 'Basic ' + base64.b64encode(client_id + ":" + client_secret))
	body = "grant_type=client_credentials"
	response = json.load(urllib2.urlopen(req, data=body))
	access_token = response.get('access_token')
	token_type = response.get('token_type')
	return token_type + " " + access_token

def get_spring_cloud_config(service, appinfo):
	if log_level > 1:
		print >> sys.stderr, "spring-cloud-config:"
		json.dump(service, sys.stderr, indent=4)
		print >> sys.stderr
	credentials = service.get('credentials', {})
	access_token = get_access_token(credentials)
	uri = credentials.get('uri')
	if uri is None:
		print >> sys.stderr, "services of type spring-config-server must specify a uri"
		return
	uri += "/" + appinfo['name']
	uri += "/" + appinfo['profile']
	try:
		if log_level > 1:
			print >> sys.stderr, "GET", uri
		req = urllib2.Request(uri)
		if access_token is not None:
			req.add_header('Authorization', access_token)
		config = json.load(urllib2.urlopen(req))
	except urllib2.URLError as err:
		print >> sys.stderr, err
		return
	if log_level > 1:
		json.dump(config, sys.stderr, indent=4)
		print >> sys.stderr
	save_config_properties(service, config)

def save_config_properties(service, config):
	#
	# Targets are configurable through VCAPX_CONFIG
	# Provided defaults direct properties to various places
	# based on simple pattern matching.
	#
	default_target = 'env'
	default_targets = [
		{
			'filter': '[0-9A-Z_]+$',
			'target': 'env',
		},
		{
			'filter': '([a-z0-9]+\\.)+[a-z0-9]+$',
			'target': 'file:config-server.properties',
			'format': 'properties',
		},
		{
			'filter': '[a-z0-9]+$',
			'target': 'file:config-server.yml',
			'format': 'yml',
		}
	]
	targets = vcap_config.get('targets', default_targets)
	#
	# Iterate through the properties and stick them in dicts for all
	# the targets that match the property.
	#
	# We iterate through the properties in reversed order, as it looks like
	# the Spring Cloud Config Server always returns the most specific context
	# first. So this order leads to the correct merge result if the same
	# property appears in multiple contexts.
	#
	for sources in reversed(config.get('propertySources', [])):
		for key, value in sources.get('source', {}).items():
			used = False
			for target in targets:
				match = re.match(target.get('filter', '.*'), key)
				if match is not None:
					used = True
					target['target'] = target.get('target', 'stderr')
					target['properties'] = target.get('properties', {})
					target['properties'][key] = value
					if log_level > 1:
						print >> sys.stderr, key, "->", target['target']
			if not used and log_level > 0:
				print >> sys.stderr, "Property", key, "was ignored because it did not match any target"
	#
	# Now iterate through the dicts and save the properties in the proper places
	#
	for target in targets:
		properties = target.get('properties', {}).items()
		if len(properties) < 1:
			continue
		destination = target.get('target', 'stderr')
		if destination == 'env':
			for key, value in properties:
				add_environment_variable(key, value)
		elif destination == 'stderr':
			write_property_file(sys.stderr, properties, target.get('format', 'text'))
		elif destination == 'stdout':
			write_property_file(sys.stdout, properties, target.get('format', 'text'))
		elif destination.startswith('file:'):
			filename = destination[5:]
			parts = filename.rsplit('.', 1)
			format = target.get('format', parts[1] if len(parts) > 1 else 'properties')
			with open(filename, 'wb') as property_file:
				write_property_file(property_file, properties, format)
		else:
			print >> sys.stderr, "Illegal target type", destination, "in VCAPX_CONFIG"
	#
	# And update VCAP_CONFIG to reflect downloaded properties
	#
	vcap_config['targets'] = targets
	add_environment_variable('VCAP_CONFIG', json.dumps(vcap_config))

def write_property_file(file, properties, format):
	if format == 'json':
		json.dump(properties, file, indent=4)
	elif format == 'yml':
		print >> file, '---'
		for key, value in properties:
			print >> file, key, value
	elif format in [ 'properties', 'text' ]:
		for key, value in properties:
			print >> file, key + '=' + value
	else:
		print >> sys.stderr, "Illegal format", format, "in VCAPX_CONFIG"

def add_environment_variable(key, value):
	#
	# There's no point sticking the property into our own environment
	# since we are a child of the process we want to affect. So instead,
	# for environment variables, we depend on our caller to set and
	# export the real environment variables. We simply place them on our
	# stdout for the caller to consume.
	#
	print key, value

if __name__ == "__main__":
	main()
