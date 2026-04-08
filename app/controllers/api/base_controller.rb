module Api
  class BaseController < ActionController::Base
    protect_from_forgery with: :null_session

    private

    def worker_token
      request.headers["X-Worker-Token"].to_s
    end

    def expected_worker_token
      ENV.fetch("WORKER_SHARED_TOKEN", "")
    end

    def require_worker_auth!
      expected = expected_worker_token
      provided = worker_token

      return if expected.present? && provided.present? && ActiveSupport::SecurityUtils.secure_compare(expected, provided)

      render json: { error: "unauthorized" }, status: :unauthorized
    end
  end
end