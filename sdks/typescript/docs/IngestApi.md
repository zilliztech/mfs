# IngestApi

All URIs are relative to *http://localhost*

| Method | HTTP request | Description |
|------------- | ------------- | -------------|
| [**addSource**](IngestApi.md#addsource) | **POST** /v1/add | Add |
| [**cancelJob**](IngestApi.md#canceljob) | **POST** /v1/jobs/{job_id}/cancel | Cancel Job |
| [**filesManifest**](IngestApi.md#filesmanifest) | **POST** /v1/files/manifest | Files Manifest |
| [**filesUpload**](IngestApi.md#filesupload) | **PUT** /v1/files/upload | Files Upload |
| [**getJob**](IngestApi.md#getjob) | **GET** /v1/jobs/{job_id} | Job |
| [**listJobs**](IngestApi.md#listjobs) | **GET** /v1/jobs | List Jobs |
| [**uploadSource**](IngestApi.md#uploadsource) | **POST** /v1/upload | Upload |



## addSource

> AddResponse addSource(addRequest)

Add

### Example

```ts
import {
  Configuration,
  IngestApi,
} from '@mfs/sdk';
import type { AddSourceRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new IngestApi(config);

  const body = {
    // AddRequest
    addRequest: ...,
  } satisfies AddSourceRequest;

  try {
    const data = await api.addSource(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **addRequest** | [AddRequest](AddRequest.md) |  | |

### Return type

[**AddResponse**](AddResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## cancelJob

> CancelResponse cancelJob(jobId)

Cancel Job

### Example

```ts
import {
  Configuration,
  IngestApi,
} from '@mfs/sdk';
import type { CancelJobRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new IngestApi(config);

  const body = {
    // string
    jobId: jobId_example,
  } satisfies CancelJobRequest;

  try {
    const data = await api.cancelJob(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **jobId** | `string` |  | [Defaults to `undefined`] |

### Return type

[**CancelResponse**](CancelResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## filesManifest

> ManifestResponse filesManifest(manifestRequest)

Files Manifest

Manifest-diff upload step ②: stat-only manifest in, need_sha1 + deletion candidates out. No bytes transferred here.

### Example

```ts
import {
  Configuration,
  IngestApi,
} from '@mfs/sdk';
import type { FilesManifestRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new IngestApi(config);

  const body = {
    // ManifestRequest
    manifestRequest: ...,
  } satisfies FilesManifestRequest;

  try {
    const data = await api.filesManifest(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **manifestRequest** | [ManifestRequest](ManifestRequest.md) |  | |

### Return type

[**ManifestResponse**](ManifestResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: `application/json`
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## filesUpload

> AddResponse filesUpload(clientId, root, process, full)

Files Upload

Manifest-diff upload step ④: PUT a tar(.gz) carrying a &#x60;.mfs-meta.json&#x60; member (hashes/renames/deletions) + the changed file bytes. The server applies it to the staging area and triggers the file-connector sync. full&#x3D;true (--force-index/--force-upload) forces a re-index of the whole staged tree.

### Example

```ts
import {
  Configuration,
  IngestApi,
} from '@mfs/sdk';
import type { FilesUploadRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new IngestApi(config);

  const body = {
    // string
    clientId: clientId_example,
    // string
    root: root_example,
    // boolean (optional)
    process: true,
    // boolean (optional)
    full: true,
  } satisfies FilesUploadRequest;

  try {
    const data = await api.filesUpload(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **clientId** | `string` |  | [Defaults to `undefined`] |
| **root** | `string` |  | [Defaults to `undefined`] |
| **process** | `boolean` |  | [Optional] [Defaults to `true`] |
| **full** | `boolean` |  | [Optional] [Defaults to `false`] |

### Return type

[**AddResponse**](AddResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## getJob

> JobResponse getJob(jobId)

Job

### Example

```ts
import {
  Configuration,
  IngestApi,
} from '@mfs/sdk';
import type { GetJobRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new IngestApi(config);

  const body = {
    // string
    jobId: jobId_example,
  } satisfies GetJobRequest;

  try {
    const data = await api.getJob(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **jobId** | `string` |  | [Defaults to `undefined`] |

### Return type

[**JobResponse**](JobResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## listJobs

> Array&lt;JobResponse&gt; listJobs(limit)

List Jobs

### Example

```ts
import {
  Configuration,
  IngestApi,
} from '@mfs/sdk';
import type { ListJobsRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new IngestApi(config);

  const body = {
    // number (optional)
    limit: 56,
  } satisfies ListJobsRequest;

  try {
    const data = await api.listJobs(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **limit** | `number` |  | [Optional] [Defaults to `20`] |

### Return type

[**Array&lt;JobResponse&gt;**](JobResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)


## uploadSource

> AddResponse uploadSource(name, process)

Upload

CS upload flow: POST a tar(.gz) of a tree as the raw body (?name&#x3D;&lt;label&gt;); the server stages + indexes it. For client/server without a shared filesystem.

### Example

```ts
import {
  Configuration,
  IngestApi,
} from '@mfs/sdk';
import type { UploadSourceRequest } from '@mfs/sdk';

async function example() {
  console.log("🚀 Testing @mfs/sdk SDK...");
  const config = new Configuration({ 
    // Configure HTTP bearer authorization: BearerAuth
    accessToken: "YOUR BEARER TOKEN",
  });
  const api = new IngestApi(config);

  const body = {
    // string
    name: name_example,
    // boolean (optional)
    process: true,
  } satisfies UploadSourceRequest;

  try {
    const data = await api.uploadSource(body);
    console.log(data);
  } catch (error) {
    console.error(error);
  }
}

// Run the test
example().catch(console.error);
```

### Parameters


| Name | Type | Description  | Notes |
|------------- | ------------- | ------------- | -------------|
| **name** | `string` |  | [Defaults to `undefined`] |
| **process** | `boolean` |  | [Optional] [Defaults to `true`] |

### Return type

[**AddResponse**](AddResponse.md)

### Authorization

[BearerAuth](../README.md#BearerAuth)

### HTTP request headers

- **Content-Type**: Not defined
- **Accept**: `application/json`


### HTTP response details
| Status code | Description | Response headers |
|-------------|-------------|------------------|
| **200** | Successful Response |  -  |
| **422** | Validation Error |  -  |
| **400** | Bad Request |  -  |
| **401** | Unauthorized |  -  |
| **404** | Not Found |  -  |
| **405** | Method Not Allowed |  -  |
| **500** | Internal Server Error |  -  |

[[Back to top]](#) [[Back to API list]](../README.md#api-endpoints) [[Back to Model list]](../README.md#models) [[Back to README]](../README.md)

