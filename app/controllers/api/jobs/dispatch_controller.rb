module Api
  module Jobs
    class DispatchController < Api::BaseController
      before_action :require_worker_auth!
      before_action :set_prompt_request, only: [ :heartbeat, :chunk, :result ]

      def claim
        timeout_seconds = params.fetch(:timeout_seconds, PromptRequest::LONG_POLL_MAX_SECONDS)
        lease_seconds = params.fetch(:lease_seconds, PromptRequest::LEASE_DEFAULT_SECONDS)

        prompt = PromptRequest.long_poll_claim(timeout_seconds: timeout_seconds, lease_seconds: lease_seconds)
        return head :no_content unless prompt

        render json: {
          id: prompt.id,
          prompt: prompt.prompt_text,
          metadata: prompt.metadata_json,
          lease_token: prompt.lease_token,
          lease_expires_at: prompt.lease_expires_at,
          attempts: prompt.attempts
        }
      end

      def heartbeat
        lease_token = params.require(:lease_token).to_s
        return render_lease_error unless @prompt_request.valid_lease?(lease_token)

        lease_seconds = params.fetch(:lease_seconds, PromptRequest::LEASE_DEFAULT_SECONDS)
        @prompt_request.extend_lease!(lease_seconds: lease_seconds)

        render json: {
          id: @prompt_request.id,
          lease_expires_at: @prompt_request.lease_expires_at
        }
      end

      def chunk
        lease_token = params.require(:lease_token).to_s
        return render_lease_error unless @prompt_request.valid_lease?(lease_token)

        @prompt_request.append_response_chunk!(params.require(:chunk).to_s)
        render json: { id: @prompt_request.id, status: @prompt_request.status }, status: :ok
      end

      def result
        lease_token = params.require(:lease_token).to_s
        return render_lease_error unless @prompt_request.valid_lease?(lease_token)

        idempotency_key = request.headers["Idempotency-Key"].to_s.presence

        if idempotency_key.present? && @prompt_request.result_idempotency_key.present?
          if ActiveSupport::SecurityUtils.secure_compare(@prompt_request.result_idempotency_key, idempotency_key)
            return render json: { id: @prompt_request.id, status: @prompt_request.status }, status: :ok
          end

          return render json: { error: "idempotency_conflict" }, status: :conflict
        end

        if ActiveModel::Type::Boolean.new.cast(params[:success])
          response_text = params[:response].presence || @prompt_request.response_text.to_s
          @prompt_request.finish_success!(response_text: response_text.to_s, idempotency_key: idempotency_key)
        else
          @prompt_request.finish_failure!(
            error_message: params.fetch(:error, "worker_failed").to_s,
            idempotency_key: idempotency_key
          )
        end

        render json: { id: @prompt_request.id, status: @prompt_request.status }, status: :ok
      end

      private

      def set_prompt_request
        @prompt_request = PromptRequest.find(params[:id])
      end

      def render_lease_error
        render json: { error: "invalid_lease" }, status: :conflict
      end
    end
  end
end