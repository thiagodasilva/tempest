import json
import httplib2
import logging
import sys
from tempest import exceptions


class RestClient(object):

    def __init__(self, config, user, key, auth_url, service, tenant_name=None):
        self.log = logging.getLogger(__name__)
        self.log.setLevel(logging.ERROR)
        self.config = config
        if self.config.env.authentication == 'keystone_v2':
            self.token, self.base_url = self.keystone_v2_auth(user,
                                                              key,
                                                              auth_url,
                                                              service,
                                                              tenant_name)
        else:
            self.token, self.base_url = self.basic_auth(user,
                                                        key,
                                                        auth_url)

    def basic_auth(self, user, api_key, auth_url):
        """
        Provides authentication for the target API
        """

        params = {}
        params['headers'] = {'User-Agent': 'Test-Client', 'X-Auth-User': user,
                             'X-Auth-Key': api_key}

        self.http_obj = httplib2.Http()
        resp, body = self.http_obj.request(auth_url, 'GET', **params)
        try:
            return resp['x-auth-token'], resp['x-server-management-url']
        except:
            raise

    def keystone_v2_auth(self, user, api_key, auth_url, service, tenant_name):
        """
        Provides authentication via Keystone 2.0
        """

        creds = {'auth': {
                'passwordCredentials': {
                    'username': user,
                    'password': api_key,
                },
                'tenantName': tenant_name
            }
        }

        self.http_obj = httplib2.Http()
        headers = {'Content-Type': 'application/json'}
        body = json.dumps(creds)
        resp, body = self.http_obj.request(auth_url, 'POST',
                                           headers=headers, body=body)

        if resp.status == 200:
            try:
                auth_data = json.loads(body)['access']
                token = auth_data['token']['id']
            except Exception, e:
                print "Failed to obtain token for user: %s" % e
                raise

            mgmt_url = None
            for ep in auth_data['serviceCatalog']:
                if ep["name"] == service:
                    mgmt_url = ep['endpoints'][0]['publicURL']
                    # See LP#920817. The tenantId is *supposed*
                    # to be returned for each endpoint accorsing to the
                    # Keystone spec. But... it isn't, so we have to parse
                    # the tenant ID out of hte public URL :(
                    tenant_id = mgmt_url.split('/')[-1]
                    break

            if mgmt_url == None:
                raise exceptions.EndpointNotFound(service)

            #TODO (dwalleck): This is a horrible stopgap.
            #Need to join strings more cleanly
            temp = mgmt_url.rsplit('/')
            service_url = temp[0] + '//' + temp[2] + '/' + temp[3] + '/'
            management_url = service_url + tenant_id
            return token, management_url
        elif resp.status == 401:
            raise exceptions.AuthenticationFailure(user=user, password=api_key)

    def post(self, url, body, headers):
        return self.request('POST', url, headers, body)

    def get(self, url):
        return self.request('GET', url)

    def delete(self, url):
        return self.request('DELETE', url)

    def put(self, url, body, headers):
        return self.request('PUT', url, headers, body)

    def _log(self, req_url, body, resp, resp_body):
        self.log.error('Request URL: ' + req_url)
        self.log.error('Request Body: ' + str(body))
        self.log.error('Response Headers: ' + str(resp))
        self.log.error('Response Body: ' + str(resp_body))

    def request(self, method, url, headers=None, body=None):
        """A simple HTTP request interface."""

        self.http_obj = httplib2.Http()
        if headers == None:
            headers = {}
        headers['X-Auth-Token'] = self.token

        req_url = "%s/%s" % (self.base_url, url)
        resp, resp_body = self.http_obj.request(req_url, method,
                                           headers=headers, body=body)

        if resp.status == 404:
            self._log(req_url, body, resp, resp_body)
            raise exceptions.NotFound(resp_body)

        if resp.status == 400:
            resp_body = json.loads(resp_body)
            self._log(req_url, body, resp, resp_body)
            raise exceptions.BadRequest(resp_body['badRequest']['message'])

        if resp.status == 413:
            resp_body = json.loads(resp_body)
            self._log(req_url, body, resp, resp_body)
            if 'overLimit' in resp_body:
                raise exceptions.OverLimit(resp_body['overLimit']['message'])
            else:
                raise exceptions.RateLimitExceeded(
                    message=resp_body['overLimitFault']['message'],
                    details=resp_body['overLimitFault']['details'])

        if resp.status in (500, 501):
            resp_body = json.loads(resp_body)
            self._log(req_url, body, resp, resp_body)
            #I'm seeing both computeFault and cloudServersFault come back.
            #Will file a bug to fix, but leave as is for now.

            if 'cloudServersFault' in resp_body:
                message = resp_body['cloudServersFault']['message']
            else:
                message = resp_body['computeFault']['message']
            raise exceptions.ComputeFault(message)

        return resp, resp_body
