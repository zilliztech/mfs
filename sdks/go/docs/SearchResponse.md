# SearchResponse

## Properties

Name | Type | Description | Notes
------------ | ------------- | ------------- | -------------
**Results** | [**[]ResultEnvelope**](ResultEnvelope.md) |  | 

## Methods

### NewSearchResponse

`func NewSearchResponse(results []ResultEnvelope, ) *SearchResponse`

NewSearchResponse instantiates a new SearchResponse object
This constructor will assign default values to properties that have it defined,
and makes sure properties required by API are set, but the set of arguments
will change when the set of required properties is changed

### NewSearchResponseWithDefaults

`func NewSearchResponseWithDefaults() *SearchResponse`

NewSearchResponseWithDefaults instantiates a new SearchResponse object
This constructor will only assign default values to properties that have it defined,
but it doesn't guarantee that properties required by API are set

### GetResults

`func (o *SearchResponse) GetResults() []ResultEnvelope`

GetResults returns the Results field if non-nil, zero value otherwise.

### GetResultsOk

`func (o *SearchResponse) GetResultsOk() (*[]ResultEnvelope, bool)`

GetResultsOk returns a tuple with the Results field if it's non-nil, zero value otherwise
and a boolean to check if the value has been set.

### SetResults

`func (o *SearchResponse) SetResults(v []ResultEnvelope)`

SetResults sets Results field to given value.



[[Back to Model list]](../README.md#documentation-for-models) [[Back to API list]](../README.md#documentation-for-api-endpoints) [[Back to README]](../README.md)


