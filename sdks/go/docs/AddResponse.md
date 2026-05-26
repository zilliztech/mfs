# AddResponse

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**JobId** | **string** | sync job id; poll GET /v1/jobs/{job_id} | 

## Methods

### NewAddResponse

`func NewAddResponse(jobId string, ) *AddResponse`

NewAddResponse instantiates a new AddResponse object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewAddResponseWithDefaults

`func NewAddResponseWithDefaults() *AddResponse`

NewAddResponseWithDefaults instantiates a new AddResponse object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetJobId

`func (o *AddResponse) GetJobId() string`

GetJobId returns the JobId field if non-nil, zero value otherwise.

### GetJobIdOk

`func (o *AddResponse) GetJobIdOk() (*string, bool)`

GetJobIdOk returns a tuple with the JobId field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetJobId

`func (o *AddResponse) SetJobId(v string)`

SetJobId sets JobId field to given value.



[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


