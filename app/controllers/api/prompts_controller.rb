module Api
  class PromptsController < BaseController
    def create
      prompt = PromptRequest.create!(
        prompt_text: params.require(:prompt).to_s,
        metadata_json: params[:metadata].presence
      )

      render json: serialize_prompt(prompt), status: :created
    end

    def show
      prompt = PromptRequest.find(params[:id])
      render json: serialize_prompt(prompt)
    end

    private

    def serialize_prompt(prompt)
      {
        id: prompt.id,
        status: prompt.status,
        prompt: prompt.prompt_text,
        response: prompt.response_text,
        error: prompt.error_message,
        created_at: prompt.created_at,
        updated_at: prompt.updated_at,
        completed_at: prompt.completed_at,
        failed_at: prompt.failed_at
      }
    end
  end
end